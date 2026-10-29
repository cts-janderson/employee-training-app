import json
import socket
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.mail import send_mail
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .accounts import send_account_email
from .mail import (AmbiguousMailError, GraphMailSender, NoAnswer, NotSent, PermanentMailError, TransientMailError,
                   https_post)
from .models import Employee, Notification, WorkerHeartbeat
from .notifications import (AmbiguousDeliveryError, GraphNotificationSender, PermanentDeliveryError,
                            TransientDeliveryError, delivery_result)

TOKEN = (200, {}, json.dumps({"access_token": "token-1", "expires_in": 3600}).encode())
ACCEPTED = (202, {"request-id": "req-1"}, b"")


class FakeGraph:
    """Answers each POST from a queue; an exception instance in the queue is raised instead."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, *, body, headers, timeout):
        self.calls.append((url, body, headers))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def sender(fake, **kwargs):
    return GraphMailSender(tenant_id="tenant", client_id="client", client_secret="secret",
                           sender="training@corp.example", timeout=5, post=fake, **kwargs)


class GraphMailSenderTests(SimpleTestCase):
    def setUp(self):
        GraphMailSender._token_cache.clear()

    def send(self, fake):
        return sender(fake).send(recipient="sam@corp.example", subject="Hi", body="Body", delivery_key="key-1")

    def test_accepted_message_returns_request_id_and_reuses_the_token(self):
        fake = FakeGraph(TOKEN, ACCEPTED, ACCEPTED)
        self.assertEqual(self.send(fake), "req-1")
        self.send(fake)
        urls = [call[0] for call in fake.calls]
        self.assertEqual(sum("oauth2/v2.0/token" in url for url in urls), 1)
        message = json.loads(fake.calls[1][1])["message"]
        self.assertEqual(message["toRecipients"][0]["emailAddress"]["address"], "sam@corp.example")
        self.assertEqual(message["body"], {"contentType": "Text", "content": "Body"})
        self.assertEqual(message["internetMessageHeaders"][0]["value"], "key-1")
        self.assertIn("/users/training@corp.example/sendMail", urls[1])

    def test_expired_token_is_refreshed_once_then_refusal_is_permanent(self):
        fake = FakeGraph(TOKEN, (401, {}, b""), TOKEN, ACCEPTED)
        self.assertEqual(self.send(fake), "req-1")
        GraphMailSender._token_cache.clear()
        with self.assertRaises(PermanentMailError):
            self.send(FakeGraph(TOKEN, (401, {}, b""), TOKEN, (401, {}, b"")))

    def test_status_classes(self):
        cases = [((429, {"retry-after": "120"}, b""), TransientMailError), ((503, {}, b""), TransientMailError),
                 ((500, {}, b""), AmbiguousMailError), ((502, {}, b""), AmbiguousMailError),
                 ((504, {}, b""), AmbiguousMailError), ((400, {}, b""), PermanentMailError),
                 ((403, {}, b""), PermanentMailError), ((404, {}, b""), PermanentMailError)]
        for answer, error in cases:
            GraphMailSender._token_cache.clear()
            with self.subTest(status=answer[0]), self.assertRaises(error):
                self.send(FakeGraph(TOKEN, answer))
        with self.assertRaises(TransientMailError) as raised:
            self.send(FakeGraph((429, {"retry-after": "120"}, b"")))
        self.assertEqual(raised.exception.retry_after, 120)

    def test_unsent_requests_retry_but_unanswered_ones_are_ambiguous(self):
        with self.assertRaises(TransientMailError):
            self.send(FakeGraph(TOKEN, NotSent("ConnectionRefusedError")))
        with self.assertRaises(AmbiguousMailError):
            self.send(FakeGraph(NoAnswer("TimeoutError")))  # token cached from the previous send

    def test_malformed_token_responses_are_retryable(self):
        for body in (b"not json", b"{}", b'{"access_token": "t", "expires_in": "soon"}', b"[]"):
            GraphMailSender._token_cache.clear()
            with self.subTest(body=body), self.assertRaises(TransientMailError) as raised:
                self.send(FakeGraph((200, {}, body)))
            self.assertEqual(raised.exception.code, "graph_token_malformed")

    def test_url_segments_are_quoted(self):
        fake = FakeGraph(TOKEN, ACCEPTED)
        GraphMailSender(tenant_id="ten/ant?x", client_id="c", client_secret="s", sender="a b@corp.example",
                        timeout=5, post=fake).send(recipient="sam@corp.example", subject="s", body="b")
        self.assertIn("/ten%2Fant%3Fx/oauth2", fake.calls[0][0])
        self.assertIn("/users/a%20b@corp.example/sendMail", fake.calls[1][0])

    def test_token_failures_never_count_as_sent(self):
        with self.assertRaises(TransientMailError):
            self.send(FakeGraph(NoAnswer("TimeoutError")))
        with self.assertRaises(PermanentMailError):
            self.send(FakeGraph((400, {}, b'{"error": "invalid_client"}')))


class HttpsPostTests(SimpleTestCase):
    def test_refused_connection_is_not_sent(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with self.assertRaises(NotSent):
            https_post(f"https://127.0.0.1:{port}/x", body="{}", headers={}, timeout=2)

    def test_failures_after_the_request_is_written_are_no_answer(self):
        for failing_step in ("request", "getresponse"):
            class Connection:
                def __init__(self, *args, **kwargs):
                    pass

                def connect(self):
                    pass

                def request(self, *args, **kwargs):
                    if failing_step == "request":
                        raise BrokenPipeError()

                def getresponse(self):
                    raise TimeoutError()

                def close(self):
                    pass

            with self.subTest(step=failing_step), self.assertRaises(NoAnswer):
                https_post("https://graph.example/x", body="{}", headers={}, timeout=2, connection_class=Connection)


class GraphNotificationSenderTests(SimpleTestCase):
    def test_mail_errors_become_delivery_errors(self):
        cases = [(TransientMailError("graph_429", 60), TransientDeliveryError),
                 (PermanentMailError("graph_400"), PermanentDeliveryError),
                 (AmbiguousMailError("graph_no_answer"), AmbiguousDeliveryError)]
        for mail_error, delivery_error in cases:
            class Failing:
                def send(self, **kwargs):
                    raise mail_error
            with self.subTest(error=type(mail_error).__name__), self.assertRaises(delivery_error):
                GraphNotificationSender(Failing()).send(delivery_key="k", recipient="sam@corp.example",
                                                        subject="s", body="b")

    def test_reserved_test_addresses_are_never_sent_to_graph(self):
        with self.assertRaises(PermanentDeliveryError):
            GraphNotificationSender(object()).send(delivery_key="k", recipient="maya@example.test",
                                                   subject="s", body="b")

    def test_retry_after_lengthens_the_backoff(self):
        notification = Notification(automatic_failures=0)
        now = timezone.now()
        _, _, changes = delivery_result(notification, TransientDeliveryError("graph_429", 1800), now=now)
        self.assertEqual(changes["next_attempt_at"], now + timedelta(seconds=1800))
        # Our own backoff cap doesn't shorten a longer wait the provider asked for.
        _, _, changes = delivery_result(notification, TransientDeliveryError("graph_429", 7200), now=now)
        self.assertEqual(changes["next_attempt_at"], now + timedelta(seconds=7200))
        _, _, changes = delivery_result(notification, TransientDeliveryError("graph_503"), now=now)
        self.assertEqual(changes["next_attempt_at"], now + timedelta(seconds=60))


class RetryAfterParsingTests(SimpleTestCase):
    def test_seconds_and_http_dates(self):
        from datetime import datetime, timezone as dt_timezone
        from .mail import _retry_after
        now = datetime(2026, 10, 29, 12, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(_retry_after({"retry-after": "120"}, now), 120)
        self.assertEqual(_retry_after({"retry-after": "Thu, 29 Oct 2026 14:00:00 GMT"}, now), 7200)
        self.assertEqual(_retry_after({"retry-after": "Thu, 29 Oct 2026 11:00:00 GMT"}, now), 0)
        self.assertIsNone(_retry_after({"retry-after": "soon"}, now))
        self.assertIsNone(_retry_after({"retry-after": "\u00b2"}, now))
        self.assertIsNone(_retry_after({}, now))


class LostClaimTests(TestCase):
    def test_worker_survives_a_claim_recovered_by_another_worker(self):
        from .notifications import NotificationError, process_ready_notifications
        claims = iter([("first", "token"), ("second", "token"), None])
        with mock.patch("training.notifications.claim_next_notification", lambda now: next(claims)), \
                mock.patch("training.notifications.dispatch_claimed",
                           side_effect=[NotificationError("lost"), "sent"]):
            self.assertEqual(process_ready_notifications(sender=object()), ["sent"])


class FailingBackend:
    def __init__(self, *args, **kwargs):
        pass

    def send_messages(self, messages):
        raise TransientMailError("graph_unreachable")


class AmbiguousBackend(FailingBackend):
    def send_messages(self, messages):
        raise AmbiguousMailError("graph_no_answer")


@override_settings(EMAIL_BACKEND="training.test_mail.FailingBackend")
class AccountEmailFailureTests(TestCase):
    def test_failed_send_keeps_the_earlier_link_working(self):
        from .accounts import setup_tokens
        employee = Employee.objects.create(name="Ivy", email="ivy@corp.example")
        earlier = setup_tokens.make_token(employee)
        with self.assertLogs("training.accounts", "ERROR"), self.captureOnCommitCallbacks(execute=True):
            send_account_email(employee, "setup")
        employee.refresh_from_db()
        self.assertEqual(employee.link_generation, 0)
        self.assertTrue(setup_tokens.check_token(employee, earlier))

    def test_ambiguous_send_keeps_the_new_link(self):
        employee = Employee.objects.create(name="Ivy", email="ivy@corp.example")
        with override_settings(EMAIL_BACKEND="training.test_mail.AmbiguousBackend"), \
                self.assertLogs("training.accounts", "ERROR"), self.captureOnCommitCallbacks(execute=True):
            send_account_email(employee, "setup")
        employee.refresh_from_db()
        self.assertEqual(employee.link_generation, 1)
        self.assertIsNotNone(employee.link_send_failed_at)

    def test_existing_account_notice_does_not_clear_a_failed_link(self):
        employee = Employee.objects.create(name="Ivy", email="ivy@corp.example", link_send_failed_at=timezone.now())
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            with self.captureOnCommitCallbacks(execute=True):
                send_account_email(employee, "existing")
        employee.refresh_from_db()
        self.assertIsNotNone(employee.link_send_failed_at)

    def test_failed_account_email_is_flagged_not_raised(self):
        employee = Employee.objects.create(name="Ivy", email="ivy@corp.example")
        with self.assertLogs("training.accounts", "ERROR") as logs, self.captureOnCommitCallbacks(execute=True):
            send_account_email(employee, "setup")
        employee.refresh_from_db()
        self.assertIsNotNone(employee.link_send_failed_at)
        self.assertNotIn("/account/setup/", "\n".join(logs.output))
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            with self.captureOnCommitCallbacks(execute=True):
                send_account_email(employee, "setup")
        employee.refresh_from_db()
        self.assertIsNone(employee.link_send_failed_at)
        self.assertEqual(len(mail.outbox), 1)


@override_settings(EMAIL_BACKEND="training.mail.GraphEmailBackend")
class GraphEmailBackendTests(SimpleTestCase):
    def test_django_mail_goes_through_graph(self):
        GraphMailSender._token_cache.clear()
        fake = FakeGraph(TOKEN, ACCEPTED)
        with mock.patch("training.mail.GraphMailSender", lambda: sender(fake)):
            self.assertEqual(send_mail("Subject", "Body", "training@corp.example", ["sam@corp.example"]), 1)
        self.assertIn("sendMail", fake.calls[1][0])


@override_settings(DEBUG=True, DEV_AUTH_STUB=True)
class NotificationWorkerTests(TestCase):
    def test_one_cycle_records_a_heartbeat(self):
        call_command("dispatch_notifications", stdout=open("/dev/null", "w"))
        self.assertTrue(WorkerHeartbeat.objects.filter(name="notifications").exists())
