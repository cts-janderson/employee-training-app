import hashlib
import json
import secrets
import threading
from io import StringIO
from unittest import skipUnless

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from webauthn.helpers import bytes_to_base64url

from .models import Employee, Passkey, PasskeyChallenge, SecurityEvent
from .sign_in_protection import DEVICE_COOKIE, record_failure
from .test_accounts import PASSWORD, limits, real_employee

ORIGIN = "http://localhost:8000"


class FakeAuthenticator:
    """A software passkey that signs real WebAuthn responses for one relying party."""

    def __init__(self, rp_id="localhost", origin=ORIGIN):
        self.rp_id, self.origin = rp_id, origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(16)
        self.user_handle = None
        self.sign_count = 0

    def _client_data(self, kind, challenge, origin=None):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin,
                           "crossOrigin": False}).encode()

    def _public_key(self):
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"),
                            -3: numbers.y.to_bytes(32, "big")})

    def create(self, options):
        self.user_handle = options["user"]["id"]
        client_data = self._client_data("webauthn.create", options["challenge"])
        credential = bytes(16) + len(self.credential_id).to_bytes(2, "big") + self.credential_id + self._public_key()
        # Flags: user present, user verified, attested credential data included.
        auth_data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x45]) + (0).to_bytes(4, "big") + credential
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {"id": bytes_to_base64url(self.credential_id), "rawId": bytes_to_base64url(self.credential_id),
                "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": bytes_to_base64url(client_data),
                             "attestationObject": bytes_to_base64url(attestation), "transports": ["internal"]}}

    def get(self, options, *, origin=None, user_handle=None, count_step=1, verified=True):
        self.sign_count += count_step
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        flags = 0x05 if verified else 0x01
        auth_data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + \
            self.sign_count.to_bytes(4, "big")
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": bytes_to_base64url(self.credential_id), "rawId": bytes_to_base64url(self.credential_id),
                "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": bytes_to_base64url(client_data),
                             "authenticatorData": bytes_to_base64url(auth_data),
                             "signature": bytes_to_base64url(signature),
                             "userHandle": user_handle or self.user_handle}}


@override_settings(APP_BASE_URL=ORIGIN)
class PasskeyTests(TestCase):
    def setUp(self):
        self.admin = real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        self.worker = real_employee("worker@example.com")
        self.admin_browser = self.browser()
        self.admin_browser.post(reverse("sign_in"), {"username": "admin@example.com", "password": PASSWORD})

    def browser(self):
        return Client(HTTP_HOST="localhost")

    def json_post(self, client, name, body, **extra):
        return client.post(reverse(name), json.dumps(body), content_type="application/json", **extra)

    def add_passkey(self, authenticator=None, client=None, name="Office laptop"):
        authenticator, client = authenticator or FakeAuthenticator(), client or self.admin_browser
        options = client.post(reverse("passkey_register_options"), {"password": PASSWORD})
        self.assertEqual(options.status_code, 200, options.content)
        response = self.json_post(client, "passkey_register_verify",
                                  {"credential": authenticator.create(options.json()), "name": name})
        self.assertEqual(response.status_code, 200, response.content)
        return authenticator

    def passkey_sign_in(self, authenticator, client=None, **kwargs):
        client = client or self.browser()
        options = client.post(reverse("passkey_sign_in_options"), REMOTE_ADDR="127.0.0.1").json()
        return client, self.json_post(client, "passkey_sign_in_verify",
                                      {"credential": authenticator.get(options, **kwargs)})

    # Adding a passkey

    def test_adding_a_passkey_needs_the_current_password(self):
        response = self.admin_browser.post(reverse("passkey_register_options"), {"password": "wrong"})
        self.assertContains(response, "isn't correct", status_code=400)
        self.add_passkey()
        passkey = Passkey.objects.get()
        self.assertEqual((passkey.employee, passkey.name), (self.admin, "Office laptop"))
        self.assertTrue(SecurityEvent.objects.filter(kind=SecurityEvent.Kind.PASSKEY_ADDED).exists())

    @override_settings(ACCOUNT_RATE_LIMITS=limits(reauthenticate=2))
    def test_wrong_password_reentries_are_limited(self):
        for _ in range(2):
            self.admin_browser.post(reverse("passkey_register_options"), {"password": "wrong"})
        response = self.admin_browser.post(reverse("passkey_register_options"), {"password": PASSWORD})
        self.assertContains(response, "Too many wrong passwords", status_code=400)

    def test_employees_who_arent_administrators_cant_add_passkeys(self):
        worker = self.browser()
        worker.post(reverse("sign_in"), {"username": "worker@example.com", "password": PASSWORD})
        response = worker.post(reverse("passkey_register_options"), {"password": PASSWORD})
        self.assertContains(response, "Only administrators", status_code=400)

    def test_account_page_warns_when_there_is_only_one_passkey(self):
        self.add_passkey()
        self.assertContains(self.admin_browser.get(reverse("account_status")), "Add a second passkey")
        self.add_passkey(name="Phone")
        self.assertNotContains(self.admin_browser.get(reverse("account_status")), "Add a second passkey")

    # Signing in with a passkey

    def test_passkey_sign_in_works_from_a_blocked_network(self):
        authenticator = self.add_passkey()
        with override_settings(ACCOUNT_RATE_LIMITS=limits(address_failures=1)):
            for _ in range(2):
                record_failure("127.0.0.1/32")
        client = self.browser()
        blocked = client.post(reverse("sign_in"), {"username": "admin@example.com", "password": PASSWORD})
        self.assertEqual(blocked.status_code, 403)
        client, response = self.passkey_sign_in(authenticator, client)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["redirect"], reverse("home"))
        self.assertEqual(client.session["_auth_user_id"], str(self.admin.pk))
        self.assertIn(DEVICE_COOKIE, response.cookies)

    def test_passkey_sign_in_is_refused_for_an_employee_no_longer_an_administrator_or_active(self):
        for change in ({"role": Employee.Role.EMPLOYEE}, {"is_active": False}):
            with self.subTest(change):
                authenticator = self.add_passkey()
                Employee.objects.filter(pk=self.admin.pk).update(**change)
                client, response = self.passkey_sign_in(authenticator)
                self.assertContains(response, "Only administrators", status_code=400)
                self.assertNotIn("_auth_user_id", client.session)
                Employee.objects.filter(pk=self.admin.pk).update(role=Employee.Role.ADMINISTRATOR, is_active=True)

    def test_a_response_naming_another_user_is_refused(self):
        authenticator = self.add_passkey()
        _, response = self.passkey_sign_in(authenticator, user_handle=bytes_to_base64url(b"someone else"))
        self.assertContains(response, "isn't recognized", status_code=400)

    def test_a_challenge_works_once_and_only_in_its_own_session(self):
        authenticator = self.add_passkey()
        client = self.browser()
        options = client.post(reverse("passkey_sign_in_options")).json()
        credential = authenticator.get(options)
        other = self.browser()
        other.post(reverse("passkey_sign_in_options"))
        self.assertContains(self.json_post(other, "passkey_sign_in_verify", {"credential": credential}),
                            "expired", status_code=400)
        self.assertEqual(self.json_post(client, "passkey_sign_in_verify", {"credential": credential}).status_code, 200)
        self.assertContains(self.json_post(client, "passkey_sign_in_verify", {"credential": credential}),
                            "expired", status_code=400)

    def test_an_expired_challenge_is_refused(self):
        authenticator = self.add_passkey()
        client = self.browser()
        options = client.post(reverse("passkey_sign_in_options")).json()
        PasskeyChallenge.objects.update(expires_at="2000-01-01T00:00:00Z")
        response = self.json_post(client, "passkey_sign_in_verify", {"credential": authenticator.get(options)})
        self.assertContains(response, "expired", status_code=400)

    def test_a_wrong_origin_a_backward_count_and_no_user_verification_are_refused(self):
        authenticator = self.add_passkey()
        _, response = self.passkey_sign_in(authenticator)
        self.assertEqual(response.status_code, 200)
        for kwargs in ({"origin": "https://evil.example"}, {"count_step": -1}, {"verified": False}):
            with self.subTest(kwargs):
                _, response = self.passkey_sign_in(authenticator, **kwargs)
                self.assertContains(response, "couldn't be verified", status_code=400)

    @override_settings(ACCOUNT_RATE_LIMITS=limits(passkey_failures=2))
    def test_failed_passkey_sign_ins_are_limited_per_passkey(self):
        authenticator = self.add_passkey()
        for _ in range(2):
            self.passkey_sign_in(authenticator, origin="https://evil.example")
        _, response = self.passkey_sign_in(authenticator)
        self.assertContains(response, "Too many failed passkey sign-ins", status_code=400)

    # Removing passkeys

    def test_removing_a_passkey_needs_the_password_and_stops_it_working(self):
        authenticator = self.add_passkey()
        passkey = Passkey.objects.get()
        self.admin_browser.post(reverse("passkey_remove", args=[passkey.pk]), {"password": "wrong"})
        self.assertTrue(Passkey.objects.exists())
        self.admin_browser.post(reverse("passkey_remove", args=[passkey.pk]), {"password": PASSWORD})
        self.assertFalse(Passkey.objects.exists())
        _, response = self.passkey_sign_in(authenticator)
        self.assertContains(response, "isn't recognized", status_code=400)

    def test_remove_passkeys_command_removes_them_and_records_it(self):
        self.add_passkey()
        out = StringIO()
        call_command("remove_passkeys", email="ADMIN@example.com", stdout=out)
        self.assertIn("Removed 1 passkey(s)", out.getvalue())
        self.assertFalse(Passkey.objects.exists())
        self.assertTrue(SecurityEvent.objects.filter(kind=SecurityEvent.Kind.PASSKEYS_COMMAND).exists())


@skipUnless(connection.vendor == "postgresql", "PostgreSQL challenge use under concurrency")
@override_settings(APP_BASE_URL=ORIGIN)
class ConcurrentPasskeyTests(TransactionTestCase):
    def test_one_response_sent_twice_at_once_signs_in_once(self):
        real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        admin = Client(HTTP_HOST="localhost")
        admin.post(reverse("sign_in"), {"username": "admin@example.com", "password": PASSWORD})
        authenticator = FakeAuthenticator()
        options = admin.post(reverse("passkey_register_options"), {"password": PASSWORD}).json()
        admin.post(reverse("passkey_register_verify"), json.dumps({"credential": authenticator.create(options),
                                                                   "name": "Laptop"}),
                   content_type="application/json")
        client = Client(HTTP_HOST="localhost")
        credential = authenticator.get(client.post(reverse("passkey_sign_in_options")).json())
        cookies = client.cookies
        barrier, statuses = threading.Barrier(2), []

        def run():
            close_old_connections()
            try:
                browser = Client(HTTP_HOST="localhost")
                browser.cookies = cookies
                barrier.wait()
                statuses.append(browser.post(reverse("passkey_sign_in_verify"),
                                             json.dumps({"credential": credential}),
                                             content_type="application/json").status_code)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(statuses), [200, 400])
