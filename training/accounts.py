"""Email and password accounts for employees; see docs/plans/29-sign-in-skeleton.md."""
import hashlib
import hmac
import logging
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX, make_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import IntegrityError, connection, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .mail import PermanentMailError, TransientMailError
from .models import Employee, RateLimitCounter

logger = logging.getLogger("training.accounts")


class AccountInputError(Exception):
    def __init__(self, message, *, field=None):
        super().__init__(message)
        self.field = field


class AccountState:
    INACTIVE = "inactive"
    UNVERIFIED = "unverified"
    PENDING = "pending"
    REJECTED = "rejected"
    ACTIVE = "active"


def account_state(employee):
    """The one access rule: only an active state reaches training pages."""
    if not employee.is_active:
        return AccountState.INACTIVE
    if employee.approval_status == Employee.Approval.REJECTED:
        return AccountState.REJECTED
    if not employee.email_is_verified:
        return AccountState.UNVERIFIED
    if employee.approval_status != Employee.Approval.APPROVED:
        return AccountState.PENDING
    return AccountState.ACTIVE


def is_account_administrator(employee):
    return (employee.role == Employee.Role.ADMINISTRATOR and not employee.is_demo
            and account_state(employee) == AccountState.ACTIVE)


def normalize_email(email):
    # NFKC matches Django's sign-in form, so rate-limit keys and lookups agree on one identity.
    return unicodedata.normalize("NFKC", email or "").strip().lower()


class EmployeeBackend(BaseBackend):
    """Authenticates real employees by case-insensitive email; demo and inactive employees can't sign in."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        email = normalize_email(username if username is not None else kwargs.get("email"))
        if not email or password is None:
            return None
        employee = Employee.objects.filter(email__iexact=email).first()
        if employee is None:
            # Hash anyway so a missing address takes as long as a wrong password.
            make_password(password)
            return None
        if employee.check_password(password) and self.user_can_authenticate(employee):
            return employee
        return None

    def user_can_authenticate(self, employee):
        return employee.is_active and not employee.is_demo

    def get_user(self, user_id):
        employee = Employee.objects.filter(pk=user_id).first()
        return employee if employee and self.user_can_authenticate(employee) else None


class _PurposeTokenGenerator(PasswordResetTokenGenerator):
    def __init__(self, purpose):
        self.key_salt = f"training.accounts.{purpose}"
        super().__init__()

    def _make_hash_value(self, employee, timestamp):
        # Changes when the password changes, the employee signs in, or a newer link is sent.
        return f"{super()._make_hash_value(employee, timestamp)}{employee.link_generation}"


class _VerificationTokenGenerator(_PurposeTokenGenerator):
    def _make_hash_value(self, employee, timestamp):
        # Changes when the address is verified or changed, or a newer link is sent.
        return f"{employee.pk}{employee.email}{employee.verified_email}{employee.link_generation}{timestamp}"


verification_tokens = _VerificationTokenGenerator("verify")
setup_tokens = _PurposeTokenGenerator("setup")
reset_tokens = _PurposeTokenGenerator("reset")


def rate_limited(scope, key, *, now=None):
    """Count a request in the scope's current fixed window and report whether the limit is exceeded."""
    limit, window = settings.ACCOUNT_RATE_LIMITS[scope]
    seconds = int((now or timezone.now()).timestamp())
    window_start = datetime.fromtimestamp(seconds - seconds % window, tz=dt_timezone.utc)
    table = connection.ops.quote_name(RateLimitCounter._meta.db_table)
    with connection.cursor() as cursor:
        # One atomic upsert, so parallel requests in any process each get their own count.
        cursor.execute(
            f"INSERT INTO {table} (scope, key_digest, window_start, count) VALUES (%s, %s, %s, 1) "
            f"ON CONFLICT (scope, key_digest, window_start) DO UPDATE SET count = {table}.count + 1 "
            "RETURNING count",
            [scope, hmac.new(settings.SECRET_KEY.encode(), key.encode(), hashlib.sha256).hexdigest(), window_start],
        )
        return cursor.fetchone()[0] > limit


def prune_rate_limits(*, now=None):
    """Delete counters whose window has ended in every scope; returns how many were removed."""
    longest = max(window for _, window in settings.ACCOUNT_RATE_LIMITS.values())
    count, _ = RateLimitCounter.objects.filter(
        window_start__lte=(now or timezone.now()) - timedelta(seconds=longest)).delete()
    return count


def _link(route, employee, tokens):
    uidb64 = urlsafe_base64_encode(force_bytes(employee.pk))
    return settings.APP_BASE_URL + reverse(route, args=[uidb64, tokens.make_token(employee)])


_EMAILS = {
    "verify": ("Confirm your email address", "verify_email", verification_tokens),
    "setup": ("Set up your training account", "account_setup", setup_tokens),
    "reset": ("Reset your training password", "password_reset_confirm", reset_tokens),
    "existing": ("Someone tried to register with your email", None, None),
}


@contextmanager
def _account_email_lock(employee_id):
    """Serialize account emails for one employee across transactions, with a PostgreSQL advisory lock."""
    if connection.vendor != "postgresql":
        yield
        return
    key = f"account-email:{employee_id}"
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", [key])
    try:
        yield
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", [key])
        except Exception:
            # A session lock outlives transactions, so drop the connection rather than leave it held.
            connection.close()
            raise


def send_account_email(employee, purpose):
    """Send after the surrounding transaction commits. A new link supersedes earlier ones."""
    subject, route, tokens = _EMAILS[purpose]

    def send():
        # One send per employee at a time. The new generation is committed before the email leaves, so a
        # delivered link survives a crash; a definite failure then restores exactly the previous one.
        with _account_email_lock(employee.pk):
            issued = None
            sending = False
            try:
                with transaction.atomic():
                    current = Employee.objects.select_for_update().get(pk=employee.pk)
                    previous = current.link_generation
                    if route:
                        current.link_generation = previous + 1
                        current.save(update_fields=["link_generation"])
                issued = current.link_generation
                link = _link(route, current, tokens) if route else settings.APP_BASE_URL + reverse("sign_in")
                body = render_to_string(f"training/email/{purpose}.txt", {"employee": current, "link": link})
                sending = True
                send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [current.email])
            except Exception as exc:
                # The response must look the same either way; administrators see the flag and can resend.
                logger.exception("Couldn't send the %s email to employee %s", purpose, employee.pk)
                if route and issued is not None:
                    # Only while this send's link is still the newest: a deactivation meanwhile must win.
                    newest = Employee.objects.filter(pk=employee.pk, link_generation=issued)
                    if not sending or isinstance(exc, (TransientMailError, PermanentMailError)):
                        # Certainly not delivered: the earlier link works again.
                        newest.update(link_generation=previous, link_send_failed_at=timezone.now())
                    else:
                        # Ambiguous or unknown: the email may have arrived, so its link must keep working.
                        newest.update(link_send_failed_at=timezone.now())
                employee.link_generation = Employee.objects.values_list("link_generation", flat=True).get(
                    pk=employee.pk)
                return
            if route:
                Employee.objects.filter(pk=employee.pk, link_generation=issued).update(link_send_failed_at=None)
            employee.link_generation = issued
        if settings.DEBUG and settings.DEV_AUTH_STUB:
            logger.info("Local %s link for %s: %s", purpose, employee.email, link)

    transaction.on_commit(send)


def redeem_link(employee_id, tokens, token):
    """Lock the employee and recheck the token, so concurrent redemptions can't both succeed.

    Call inside a transaction. Returns the locked employee, or None when the link is no longer valid."""
    employee = Employee.objects.select_for_update().filter(pk=employee_id, is_demo=False, is_active=True).first()
    if employee is None or not tokens.check_token(employee, token):
        return None
    return employee


def _validated_profile(name, email):
    name = (name or "").strip()
    email = normalize_email(email)
    if not name or len(name) > 200:
        raise AccountInputError("Enter a name of at most 200 characters.", field="name")
    try:
        validate_email(email)
    except ValidationError:
        raise AccountInputError("Enter a valid email address.", field="email") from None
    return name, email


def register(*, name, email, password):
    """Create a pending applicant, or notify the existing owner. Callers show the same response either way."""
    name, email = _validated_profile(name, email)
    applicant = Employee(name=name, email=email, approval_status=Employee.Approval.PENDING)
    try:
        validate_password(password, applicant)
    except ValidationError as exc:
        raise AccountInputError(" ".join(exc.messages), field="password") from None
    applicant.set_password(password)
    try:
        with transaction.atomic():
            if Employee.objects.filter(email__iexact=email).exists():
                raise IntegrityError
            applicant.save()
    except IntegrityError:
        existing = Employee.objects.filter(email__iexact=email).first()
        if existing is not None and not existing.is_demo:
            send_account_email(existing, "existing")
        return
    send_account_email(applicant, "verify")


def verify_email(employee_id, token, *, now=None):
    """Record mailbox control. Returns the employee, or None when the link is invalid."""
    now = now or timezone.now()
    with transaction.atomic():
        employee = redeem_link(employee_id, verification_tokens, token)
        if employee is not None:
            mark_verified(employee, now=now)
        return employee


def mark_verified(employee, *, now=None):
    if not employee.email_is_verified:
        employee.verified_email = employee.email
        employee.email_verified_at = now or timezone.now()
        employee.save(update_fields=["verified_email", "email_verified_at"])


def resend_verification(employee):
    if not employee.is_demo and not employee.email_is_verified:
        send_account_email(employee, "verify")


def request_password_reset(email):
    """Send a reset link to an active real account with a password. Callers respond neutrally."""
    employee = Employee.objects.filter(email__iexact=normalize_email(email), is_demo=False, is_active=True).first()
    if employee is not None and employee.has_usable_password():
        send_account_email(employee, "reset")


def pending_applications():
    return [employee for employee in Employee.objects.filter(
        is_demo=False, approval_status=Employee.Approval.PENDING,
    ).exclude(verified_email="").order_by("created_at", "id") if employee.email_is_verified]


def awaiting_setup():
    return Employee.objects.filter(
        is_demo=False, approval_status=Employee.Approval.APPROVED, is_active=True,
        password__startswith=UNUSABLE_PASSWORD_PREFIX,
    ).order_by("name", "id")


def _require_administrator(actor):
    if not is_account_administrator(actor):
        raise AccountInputError("Administrator access is unavailable.")


def decide_application(*, actor, employee_id, approve, now=None):
    _require_administrator(actor)
    now = now or timezone.now()
    with transaction.atomic():
        try:
            applicant = Employee.objects.select_for_update().get(pk=employee_id, is_demo=False)
        except (Employee.DoesNotExist, ValidationError, ValueError):
            raise AccountInputError("This application no longer exists.") from None
        if applicant.approval_status != Employee.Approval.PENDING:
            raise AccountInputError("This application has already been decided.")
        if not applicant.email_is_verified:
            raise AccountInputError("This applicant hasn't verified their email address yet.")
        applicant.approval_status = Employee.Approval.APPROVED if approve else Employee.Approval.REJECTED
        applicant.approval_decided_at = now
        applicant.approval_decided_by = actor
        applicant.save(update_fields=["approval_status", "approval_decided_at", "approval_decided_by"])
        return applicant


def invite_employee(*, actor, name, email, now=None):
    """Create an approved employee without a password and send a setup link."""
    _require_administrator(actor)
    name, email = _validated_profile(name, email)
    now = now or timezone.now()
    employee = Employee(name=name, email=email, approval_status=Employee.Approval.APPROVED,
                        approval_decided_at=now, approval_decided_by=actor)
    employee.set_unusable_password()
    try:
        with transaction.atomic():
            employee.save()
    except IntegrityError:
        raise AccountInputError("An employee already uses that email.", field="email") from None
    send_account_email(employee, "setup")
    return employee


def resend_setup(*, actor, employee_id):
    _require_administrator(actor)
    try:
        employee = awaiting_setup().get(pk=employee_id)
    except (Employee.DoesNotExist, ValidationError, ValueError):
        raise AccountInputError("This employee has already set up their account.") from None
    send_account_email(employee, "setup")
    return employee
