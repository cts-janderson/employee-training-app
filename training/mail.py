"""Outbound email through Microsoft Graph; see docs/adr/0011-email-through-microsoft-graph.md.

The client separates a request that never left (safe to retry) from one that left without a clear answer
(ambiguous), because Graph's sendMail has no idempotency key and a retry could send a second email.
"""
import http.client
import json
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode, urlsplit

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


class MailError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class TransientMailError(MailError):
    """The message was not accepted and was not sent; retrying is safe."""

    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.retry_after = retry_after


class PermanentMailError(MailError):
    """The message was refused; retrying won't help."""


class AmbiguousMailError(MailError):
    """The request may have been sent; only a person can tell whether the email arrived."""


class NotSent(Exception):
    """The connection failed before any request bytes were written."""


class NoAnswer(Exception):
    """The request was written, but no complete response arrived."""


def https_post(url, *, body, headers, timeout, connection_class=http.client.HTTPSConnection):
    """POST and return (status, headers, body). Raises NotSent or NoAnswer instead of socket errors."""
    parts = urlsplit(url)
    connection = connection_class(parts.hostname, parts.port or 443, timeout=timeout)
    try:
        try:
            connection.connect()
        except (OSError, http.client.HTTPException) as exc:
            raise NotSent(type(exc).__name__) from exc
        try:
            connection.request("POST", parts.path + (f"?{parts.query}" if parts.query else ""), body=body,
                               headers=headers)
            response = connection.getresponse()
            return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise NoAnswer(type(exc).__name__) from exc
    finally:
        connection.close()


def _retry_after(headers, now=None):
    """Seconds to wait from a Retry-After header in either form: delay-seconds or an HTTP date."""
    value = headers.get("retry-after", "").strip()
    if not value:
        return None
    if value.isascii() and value.isdigit():
        return int(value)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        return None
    return max(0, int((moment - (now or datetime.now(timezone.utc))).total_seconds()))


class GraphMailSender:
    """Send plain-text email from one mailbox with the client-credentials flow."""

    _token_lock = threading.Lock()
    _token_cache = {}

    def __init__(self, *, tenant_id=None, client_id=None, client_secret=None, sender=None, timeout=None,
                 post=https_post, clock=time.monotonic):
        self.tenant_id = tenant_id or settings.GRAPH_TENANT_ID
        self.client_id = client_id or settings.GRAPH_CLIENT_ID
        self.client_secret = client_secret or settings.GRAPH_CLIENT_SECRET
        self.sender = sender or settings.DEFAULT_FROM_EMAIL
        self.timeout = timeout or settings.MAIL_TIMEOUT_SECONDS
        self.post = post
        self.clock = clock

    def _token(self, *, refresh=False):
        key = (self.tenant_id, self.client_id)
        with self._token_lock:
            cached = self._token_cache.get(key)
            if cached and not refresh and cached[1] > self.clock():
                return cached[0]
            try:
                status, headers, body = self.post(
                    f"https://login.microsoftonline.com/{quote(self.tenant_id, safe='')}/oauth2/v2.0/token",
                    body=urlencode({"client_id": self.client_id, "client_secret": self.client_secret,
                                    "scope": "https://graph.microsoft.com/.default",
                                    "grant_type": "client_credentials"}),
                    headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=self.timeout)
            except (NotSent, NoAnswer):
                # No email was sent yet, so a failed token request is always safe to retry.
                raise TransientMailError("graph_token_unreachable") from None
            if status == 200:
                try:
                    payload = json.loads(body)
                    token = payload["access_token"]
                    lifetime = int(payload.get("expires_in", 0))
                except (ValueError, KeyError, TypeError, AttributeError):
                    # Nothing was sent, so an unreadable token response is safe to retry.
                    raise TransientMailError("graph_token_malformed") from None
                # Renew a minute early so a token never expires mid-request.
                self._token_cache[key] = (token, self.clock() + max(0, lifetime - 60))
                return token
            if status == 429 or status >= 500:
                raise TransientMailError("graph_token_unavailable", _retry_after(headers))
            raise PermanentMailError("graph_token_refused")

    def send(self, *, recipient, subject, body, delivery_key=None):
        """Return Graph's request id when the message is accepted."""
        message = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": recipient}}],
        }
        if delivery_key is not None:
            message["internetMessageHeaders"] = [{"name": "X-Training-Delivery-Key", "value": str(delivery_key)}]
        payload = json.dumps({"message": message, "saveToSentItems": False})
        refreshed = False
        while True:
            token = self._token(refresh=refreshed)
            try:
                status, headers, _ = self.post(
                    f"https://graph.microsoft.com/v1.0/users/{quote(self.sender, safe='@')}/sendMail", body=payload,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    timeout=self.timeout)
            except NotSent:
                raise TransientMailError("graph_unreachable") from None
            except NoAnswer:
                raise AmbiguousMailError("graph_no_answer") from None
            if status == 202:
                return headers.get("request-id", "graph:accepted")
            if status == 401 and not refreshed:
                refreshed = True
                continue
            if status in {429, 503}:
                # Throttled or unavailable: the request wasn't processed, so retrying is safe.
                raise TransientMailError(f"graph_{status}", _retry_after(headers))
            if status in {500, 502, 504}:
                # Exchange may have accepted the message before the error, so a retry could send it twice.
                raise AmbiguousMailError(f"graph_{status}")
            raise PermanentMailError(f"graph_{status}")


class GraphEmailBackend(BaseEmailBackend):
    """Django email backend for account emails, sent one recipient at a time through Graph."""

    def send_messages(self, email_messages):
        sender = GraphMailSender()
        sent = 0
        for message in email_messages:
            try:
                for recipient in message.recipients():
                    sender.send(recipient=recipient, subject=message.subject, body=message.body)
                sent += 1
            except MailError:
                if not self.fail_silently:
                    raise
        return sent


def graph_configured():
    return all([settings.GRAPH_TENANT_ID, settings.GRAPH_CLIENT_ID, settings.GRAPH_CLIENT_SECRET])
