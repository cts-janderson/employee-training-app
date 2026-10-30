import logging
import os
import subprocess
import sys
from ipaddress import ip_network
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.checks import run_checks
from django.core.management import call_command
from django.db import OperationalError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from gunicorn.config import Config

from config.gunicorn_logging import RedactingLogger
from config.logging import RedactAccountLinks, redact_account_links

from .sign_in_protection import client_address
from .accounts import send_account_email
from .models import Employee

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION = {"DJANGO_DEBUG": "0", "DEV_AUTH_STUB": "0", "DJANGO_SECRET_KEY": "x" * 50,
              "DJANGO_ALLOWED_HOSTS": "training.example.com", "APP_BASE_URL": "https://training.example.com",
              "DATABASE_URL": "postgresql://localhost:5432/unused",
              "GRAPH_TENANT_ID": "tenant", "GRAPH_CLIENT_ID": "client", "GRAPH_CLIENT_SECRET": "secret"}


def load_settings(**overrides):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("DJANGO_", "DEV_", "APP_", "GRAPH_", "EMAIL_"))}
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

    def test_documented_build_placeholders_load(self):
        result = load_settings(DJANGO_SECRET_KEY="build", DJANGO_ALLOWED_HOSTS="build",
                               APP_BASE_URL="https://build.invalid", DATABASE_URL="postgresql://build/unused",
                               GRAPH_TENANT_ID="build", GRAPH_CLIENT_ID="build", GRAPH_CLIENT_SECRET="build")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_requires_graph_credentials_to_send_email(self):
        result = load_settings(GRAPH_CLIENT_SECRET=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GRAPH_CLIENT_SECRET are required", result.stderr)

    def test_production_refuses_an_email_backend_that_drops_mail(self):
        result = load_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EMAIL_BACKEND must be the Graph backend", result.stderr)

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
        return client_address(RequestFactory().get("/", **extra))

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

    def test_ipv4_mapped_addresses_count_as_ipv4(self):
        self.assertEqual(self.client_for("::ffff:172.18.0.5", "::ffff:203.0.113.9"), "203.0.113.9")
        self.assertEqual(self.client_for("::ffff:198.51.100.7"), "198.51.100.7")

    @override_settings(TRUSTED_PROXY_NETWORKS=[ip_network("::ffff:172.18.0.0/112")])
    def test_a_mapped_trusted_network_still_trusts_a_mapped_proxy(self):
        self.assertEqual(self.client_for("::ffff:172.18.0.5", "203.0.113.9"), "203.0.113.9")

    @override_settings(TRUSTED_PROXY_NETWORKS=[])
    def test_without_trusted_proxies_the_header_is_ignored(self):
        self.assertEqual(self.client_for("172.18.0.5", "203.0.113.9"), "172.18.0.5")

    @override_settings(TRUSTED_PROXY_NETWORKS=[])
    def test_deployment_check_warns_without_trusted_proxies(self):
        self.assertIn("training.W001", [message.id for message in run_checks(include_deployment_checks=True)])


class RedactionTests(SimpleTestCase):
    def test_account_link_tokens_are_redacted_from_log_records(self):
        self.assertEqual(redact_account_links('"GET /account/reset/MTIz/abc-def/ HTTP/1.1" 302'),
                         '"GET /account/reset/[redacted]/ HTTP/1.1" 302')
        record = logging.LogRecord("django.server", logging.INFO, "", 0, '"%s" %s',
                                   ("GET /account/verify/MTIz/tok-en/ HTTP/1.1", 200), None)
        RedactAccountLinks().filter(record)
        self.assertNotIn("tok-en", record.getMessage())
        self.assertEqual(redact_account_links("GET /account/sign-in/"), "GET /account/sign-in/")

    def test_percent_encoded_account_links_are_redacted(self):
        self.assertEqual(redact_account_links("GET /account/%76erify/MTIz/tok-en/ HTTP/1.1"),
                         "GET /account/[redacted]/ HTTP/1.1")
        self.assertEqual(redact_account_links("GET /account/reset/MTIz%2Ftok-en/"), "GET /account/[redacted]/")
        self.assertEqual(redact_account_links("GET /search/?q=a%20b"), "GET /search/?q=a%20b")
        # Only the path is replaced; an encoded newline elsewhere stays encoded, so it can't forge a log line.
        self.assertEqual(redact_account_links("GET /account/%76erify/MTIz/tok-en/?x=%0AFORGED HTTP/1.1"),
                         "GET /account/[redacted]/?x=%0AFORGED HTTP/1.1")

    def test_tracebacks_are_redacted_too(self):
        try:
            raise ValueError("failed for /account/setup/MTIz/tok-en/")
        except ValueError:
            import sys
            record = logging.LogRecord("django.request", logging.ERROR, "", 0, "boom", (), sys.exc_info())
        RedactAccountLinks().filter(record)
        self.assertNotIn("tok-en", record.exc_text)

    def test_local_account_links_stay_readable_while_the_stub_is_on(self):
        # The printed link stands in for email locally, so the stub's logger skips redaction.
        self.assertTrue(settings.DEV_AUTH_STUB)
        self.assertEqual(settings.LOGGING["loggers"]["training.accounts"]["handlers"], ["local_links"])
        self.assertNotIn("filters", settings.LOGGING["handlers"]["local_links"])


    def test_gunicorn_access_and_error_logs_redact_account_links(self):
        logger = RedactingLogger(Config())
        with self.assertLogs("gunicorn.access", level="INFO") as access:
            logger.access_log.info('"%(r)s" %(s)s', {"r": "GET /account/setup/MTIz/tok-en/ HTTP/1.1", "s": 200})
        with self.assertLogs("gunicorn.error", level="ERROR") as error:
            logger.error_log.error("Error handling /account/reset/MTIz/tok-en/")
        self.assertEqual(access.records[0].getMessage(), '"GET /account/setup/[redacted]/ HTTP/1.1" 200')
        self.assertNotIn("tok-en", error.records[0].getMessage())


class HealthTests(TestCase):
    def test_health_reports_ok_when_the_database_answers(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_reports_unavailable_when_the_database_does_not_answer(self):
        with patch("training.views.connection.cursor", side_effect=OperationalError("down")), \
                self.assertLogs("training.views", level="WARNING"):
            response = self.client.get("/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})


class CleanupSessionTests(TestCase):
    def test_cleanup_removes_expired_sessions_and_keeps_current_ones(self):
        now = timezone.now()
        Session.objects.create(session_key="expired", session_data="", expire_date=now - timedelta(minutes=1))
        Session.objects.create(session_key="current", session_data="", expire_date=now + timedelta(hours=1))
        call_command("cleanup_training_imports", stdout=StringIO())
        self.assertEqual(list(Session.objects.values_list("session_key", flat=True)), ["current"])


class PlainTextEmailTests(TestCase):
    def test_account_email_keeps_names_unescaped(self):
        employee = Employee.objects.create(name="Ana & Bo <QA>", email="ana@example.com")
        with self.captureOnCommitCallbacks(execute=True):
            send_account_email(employee, "setup")
        self.assertIn("Hello Ana & Bo <QA>,", mail.outbox[-1].body)
