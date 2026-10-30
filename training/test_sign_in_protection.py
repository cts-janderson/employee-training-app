import threading
from datetime import timedelta
from io import StringIO
from unittest import mock, skipUnless

from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .accounts import count_request
from .models import AddressBlock, Employee, RateLimitCounter, SecurityEvent
from .sign_in_protection import (
    DEVICE_COOKIE,
    active_block,
    lift_block,
    make_permanent,
    network_of,
    normalize_network,
    record_failure,
)
from .test_accounts import PASSWORD, limits, real_employee

OTHER = "203.0.113.9"


class SignInProtectionTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        self.worker = real_employee("worker@example.com")

    def sign_in(self, email, password=PASSWORD, client=None, address="127.0.0.1"):
        client = client or self.client
        return client.post(reverse("sign_in"), {"username": email, "password": password}, REMOTE_ADDR=address,
                           HTTP_HOST="localhost")

    def browser(self):
        return Client(HTTP_HOST="localhost")

    def trusted_browser(self, email="worker@example.com"):
        browser = self.browser()
        self.assertEqual(self.sign_in(email, client=browser).status_code, 302)
        browser.post(reverse("sign_out"))
        self.assertIn(DEVICE_COOKIE, browser.cookies)
        return browser

    # Failures, not attempts

    @override_settings(ACCOUNT_RATE_LIMITS=limits(sign_in=5))
    def test_correct_password_after_four_wrong_ones_signs_in(self):
        for _ in range(4):
            self.assertEqual(self.sign_in("worker@example.com", "wrong").status_code, 200)
        self.assertEqual(self.sign_in("worker@example.com").status_code, 302)

    def test_successful_sign_in_adds_nothing_to_failure_counters(self):
        self.sign_in("worker@example.com")
        self.assertEqual(set(RateLimitCounter.objects.values_list("scope", flat=True)), {"sign_in_client"})

    # Trusted devices

    @override_settings(ACCOUNT_RATE_LIMITS=limits(sign_in_email=3))
    def test_trusted_browser_signs_in_while_its_email_is_under_attack(self):
        browser = self.trusted_browser()
        for _ in range(3):
            self.sign_in("worker@example.com", "wrong", address=OTHER)
        self.assertEqual(self.sign_in("worker@example.com", client=self.browser()).status_code, 429)
        self.assertEqual(self.sign_in("worker@example.com", client=browser).status_code, 302)

    @override_settings(ACCOUNT_RATE_LIMITS=limits(sign_in_email=1))
    def test_trusted_browser_gives_no_exemption_for_another_email(self):
        browser = self.trusted_browser()
        self.sign_in("admin@example.com", "wrong", address=OTHER)
        self.assertEqual(self.sign_in("admin@example.com", client=browser).status_code, 429)

    @override_settings(ACCOUNT_RATE_LIMITS=limits(sign_in_email=1))
    def test_password_change_and_deactivation_end_trusted_browsers(self):
        for change in ("password", "session_version"):
            with self.subTest(change):
                RateLimitCounter.objects.all().delete()
                browser = self.trusted_browser()
                worker = Employee.objects.get(pk=self.worker.pk)
                if change == "password":
                    worker.set_password(PASSWORD)
                else:
                    worker.session_version += 1
                worker.save()
                self.sign_in("worker@example.com", "wrong", address=OTHER)
                self.assertEqual(self.sign_in("worker@example.com", client=browser).status_code, 429)

    @override_settings(ACCOUNT_RATE_LIMITS=limits(sign_in_email=1), TRUSTED_DEVICE_DAYS=90)
    def test_trusted_browser_expires(self):
        browser = self.trusted_browser()
        self.sign_in("worker@example.com", "wrong", address=OTHER)
        later = timezone.now().timestamp() + 91 * 24 * 60 * 60
        with mock.patch("django.core.signing.time.time", return_value=later):
            self.assertEqual(self.sign_in("worker@example.com", client=browser).status_code, 429)

    @override_settings(ACCOUNT_RATE_LIMITS=limits(sign_in_device=2, trusted_email=3))
    def test_each_browser_has_its_own_count_and_they_share_the_email_count(self):
        first, second = self.trusted_browser(), self.trusted_browser()
        for _ in range(2):
            self.sign_in("worker@example.com", "wrong", client=first)
        self.assertEqual(self.sign_in("worker@example.com", client=first).status_code, 429)
        self.sign_in("worker@example.com", "wrong", client=second)
        self.assertEqual(self.sign_in("worker@example.com", client=second).status_code, 429)

    # Address blocks

    @override_settings(ACCOUNT_RATE_LIMITS=limits(address_failures=3, sign_in_email=100))
    def test_failures_past_the_limit_block_the_network_across_emails(self):
        for email in ("a@example.com", "b@example.com", "c@example.com"):
            self.assertEqual(self.sign_in(email, "wrong").status_code, 200)
        self.assertEqual(self.sign_in("d@example.com", "wrong").status_code, 403)
        self.assertEqual(AddressBlock.objects.get().network, "127.0.0.1/32")
        self.assertEqual(SecurityEvent.objects.get().kind, SecurityEvent.Kind.BLOCK_CREATED)
        for name, data in (("sign_in", {"username": "worker@example.com", "password": PASSWORD}),
                           ("register", {"name": "New", "email": "new@example.com", "password": PASSWORD}),
                           ("password_reset", {"email": "worker@example.com"})):
            with self.subTest(name):
                response = self.client.post(reverse(name), data, REMOTE_ADDR="127.0.0.1")
                self.assertContains(response, "Sign-in from your network is blocked", status_code=403)
        self.assertEqual(self.sign_in("worker@example.com", address=OTHER).status_code, 302)

    @override_settings(ACCOUNT_RATE_LIMITS=limits(address_failures=2))
    def test_a_missed_block_is_created_by_the_next_failure(self):
        for _ in range(4):
            count_request("address_failures", "198.51.100.1/32")
        self.assertIsNotNone(record_failure("198.51.100.1/32"))

    @override_settings(ACCOUNT_RATE_LIMITS=limits(address_failures=1), ADDRESS_BLOCK_HOURS=24)
    def test_blocks_expire_and_can_be_made_permanent(self):
        now = timezone.now()
        block = None
        for _ in range(2):
            block = record_failure("198.51.100.1/32", now=now) or block
        self.assertIsNone(active_block("198.51.100.1/32", now=now + timedelta(hours=24)))
        make_permanent(block.pk, actor=self.admin, now=now)
        self.assertIsNotNone(active_block("198.51.100.1/32", now=now + timedelta(days=365)))

    @override_settings(ACCOUNT_RATE_LIMITS=limits(address_failures=1))
    def test_an_expired_block_is_closed_before_a_new_one_opens(self):
        now = timezone.now()
        for _ in range(2):
            record_failure("198.51.100.1/32", now=now)
        later = now + timedelta(days=2)
        for _ in range(2):
            record_failure("198.51.100.1/32", now=later)
        self.assertEqual(AddressBlock.objects.filter(ended_at__isnull=True).count(), 1)
        self.assertEqual(AddressBlock.objects.get(end_reason=AddressBlock.EndReason.EXPIRED).ended_at,
                         now + timedelta(hours=24))

    @override_settings(ACCOUNT_RATE_LIMITS=limits(address_failures=1))
    def test_lifting_a_block_clears_the_network_counters(self):
        for _ in range(2):
            record_failure("198.51.100.1/32")
        lift_block("198.51.100.1/32", actor=self.admin)
        self.assertIsNone(active_block("198.51.100.1/32"))
        self.assertIsNone(record_failure("198.51.100.1/32"))

    def test_ipv6_addresses_in_one_64_share_a_network_and_mapped_addresses_are_ipv4(self):
        self.assertEqual(network_of("2001:db8:1:2::1"), network_of("2001:db8:1:2:ffff::9"))
        self.assertEqual(network_of("2001:db8:1:2::1"), "2001:db8:1:2::/64")
        self.assertEqual(network_of("::ffff:192.0.2.5"), "192.0.2.5/32")
        self.assertEqual(normalize_network("2001:db8:1:2::7"), "2001:db8:1:2::/64")

    @override_settings(ACCOUNT_RATE_LIMITS=limits(address_failures=1))
    def test_only_real_administrators_manage_blocks(self):
        for _ in range(2):
            record_failure("198.51.100.1/32")
        worker = self.browser()
        self.sign_in("worker@example.com", client=worker)
        self.assertEqual(worker.get(reverse("address_blocks")).status_code, 403)
        admin = self.browser()
        self.sign_in("admin@example.com", client=admin)
        self.assertContains(admin.get(reverse("address_blocks")), "198.51.100.1/32")
        admin.post(reverse("address_blocks"), {"action": "lift", "network": "198.51.100.1/32"})
        block = AddressBlock.objects.get()
        self.assertEqual((block.end_reason, block.ended_by), (AddressBlock.EndReason.LIFTED, self.admin))

    @override_settings(ACCOUNT_RATE_LIMITS=limits(address_failures=1))
    def test_unblock_command_lifts_a_block_and_records_it(self):
        for _ in range(2):
            record_failure("198.51.100.1/32")
        out = StringIO()
        call_command("unblock_address", "198.51.100.1", stdout=out)
        self.assertIn("Lifted the block on 198.51.100.1/32", out.getvalue())
        self.assertEqual(AddressBlock.objects.get().end_reason, AddressBlock.EndReason.COMMAND)
        self.assertTrue(SecurityEvent.objects.filter(kind=SecurityEvent.Kind.BLOCK_COMMAND).exists())

    # Reset email cap per email

    @override_settings(ACCOUNT_RATE_LIMITS=limits(account_email_target=2))
    def test_reset_requests_for_one_email_are_capped_across_networks(self):
        for address in ("198.51.100.1", "198.51.100.2"):
            self.client.post(reverse("password_reset"), {"email": "worker@example.com"}, REMOTE_ADDR=address)
        response = self.client.post(reverse("password_reset"), {"email": "worker@example.com"},
                                    REMOTE_ADDR="198.51.100.3")
        self.assertEqual(response.status_code, 429)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL advisory locks under concurrency")
@override_settings(ACCOUNT_RATE_LIMITS=limits(address_failures=3))
class ConcurrentBlockTests(TransactionTestCase):
    def test_parallel_failures_create_one_block(self):
        workers = 8
        barrier = threading.Barrier(workers)

        def run():
            close_old_connections()
            try:
                barrier.wait()
                record_failure("198.51.100.1/32")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=run) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(AddressBlock.objects.count(), 1)
