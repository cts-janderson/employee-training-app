import logging
import os
import subprocess
import sys
from ipaddress import ip_network
from pathlib import Path

from django.core import mail
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from config.logging import RedactAccountLinks, redact_account_links

from .account_views import _client
from .accounts import send_account_email
from .models import Employee

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION = {"DJANGO_DEBUG": "0", "DEV_AUTH_STUB": "0", "DJANGO_SECRET_KEY": "x" * 50,
              "DJANGO_ALLOWED_HOSTS": "training.example.com", "APP_BASE_URL": "https://training.example.com",
              "DATABASE_URL": "postgresql://localhost:5432/unused"}


def load_settings(**overrides):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("DJANGO_", "DEV_", "APP_"))}
    env.update(PRODUCTION)
    env.update(overrides)
    env = {key: value for key, value in env.items() if value is not None}
    code = "import config.settings as s; print(s.CSRF_TRUSTED_ORIGINS, s.SECURE_HSTS_SECONDS, s.SECURE_REFERRER_POLICY)"
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)


class ProductionSettingsTests(SimpleTestCase):
    def test_production_settings_load_with_trusted_origin_and_security_headers(self):
        result = load_settings()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("['https://training.example.com'] 3600 same-origin", result.stdout)

    def test_production_refuses_plain_http_base_url(self):
        result = load_settings(APP_BASE_URL="http://training.example.com")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("APP_BASE_URL must use https", result.stderr)

    def test_production_requires_explicit_allowed_hosts(self):
        result = load_settings(DJANGO_ALLOWED_HOSTS=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_ALLOWED_HOSTS is required", result.stderr)


@override_settings(TRUSTED_PROXY_NETWORKS=[ip_network("172.18.0.0/16")])
class ClientAddressTests(SimpleTestCase):
    def client_for(self, remote, forwarded=None):
        extra = {"REMOTE_ADDR": remote}
        if forwarded is not None:
            extra["HTTP_X_FORWARDED_FOR"] = forwarded
        return _client(RequestFactory().get("/", **extra))

    def test_trusted_proxy_names_the_client_with_its_last_entry(self):
        self.assertEqual(self.client_for("172.18.0.5", "203.0.113.9"), "203.0.113.9")
        # A client-supplied entry before the proxy's own can't choose the rate-limit key.
        self.assertEqual(self.client_for("172.18.0.5", "1.2.3.4, 203.0.113.9"), "203.0.113.9")
        self.assertEqual(self.client_for("172.18.0.5", "2001:db8::1"), "2001:db8::1")

    def test_untrusted_peers_and_bad_headers_fall_back_to_the_peer(self):
        self.assertEqual(self.client_for("198.51.100.7", "203.0.113.9"), "198.51.100.7")
        self.assertEqual(self.client_for("172.18.0.5", "not-an-address"), "172.18.0.5")
        self.assertEqual(self.client_for("172.18.0.5", ""), "172.18.0.5")
        self.assertEqual(self.client_for("172.18.0.5"), "172.18.0.5")

    @override_settings(TRUSTED_PROXY_NETWORKS=[])
    def test_without_trusted_proxies_the_header_is_ignored(self):
        self.assertEqual(self.client_for("172.18.0.5", "203.0.113.9"), "172.18.0.5")


class RedactionTests(SimpleTestCase):
    def test_account_link_tokens_are_redacted_from_log_records(self):
        self.assertEqual(redact_account_links('"GET /account/reset/MTIz/abc-def/ HTTP/1.1" 302'),
                         '"GET /account/reset/[redacted]/ HTTP/1.1" 302')
        record = logging.LogRecord("django.server", logging.INFO, "", 0, '"%s" %s',
                                   ("GET /account/verify/MTIz/tok-en/ HTTP/1.1", 200), None)
        RedactAccountLinks().filter(record)
        self.assertNotIn("tok-en", record.getMessage())
        self.assertEqual(redact_account_links("GET /account/sign-in/"), "GET /account/sign-in/")


class PlainTextEmailTests(TestCase):
    def test_account_email_keeps_names_unescaped(self):
        employee = Employee.objects.create(name="Ana & Bo <QA>", email="ana@example.com")
        with self.captureOnCommitCallbacks(execute=True):
            send_account_email(employee, "setup")
        self.assertIn("Hello Ana & Bo <QA>,", mail.outbox[-1].body)
