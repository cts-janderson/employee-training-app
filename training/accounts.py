"""Email and password accounts for employees; see docs/plans/29-sign-in-skeleton.md."""
import logging
import uuid
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
from django.db.models import F, ProtectedError, Q
from django.db.models.functions import Coalesce, Lower
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .mail import PermanentMailError, TransientMailError
from .models import Employee, RateLimitCounter, SecurityEvent

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


def _counter(scope, key, now):
    """The scope's limit, the start of its current fixed window, and the stored digest of the key."""
    limit, window = settings.ACCOUNT_RATE_LIMITS[scope]
    seconds = int((now or timezone.now()).timestamp())
    window_start = datetime.fromtimestamp(seconds - seconds % window, tz=dt_timezone.utc)
    return limit, window_start, _key_digest(key)


def _key_digest(key):
    return salted_hmac("training.rate_limit", key, algorithm="sha256").hexdigest()


def count_request(scope, key, *, now=None):
    """Count a request in the scope's current fixed window and return the new count."""
    _, window_start, digest = _counter(scope, key, now)
    table = connection.ops.quote_name(RateLimitCounter._meta.db_table)
    with connection.cursor() as cursor:
        # One atomic upsert, so parallel requests in any process each get their own count.
        cursor.execute(
            f"INSERT INTO {table} (scope, key_digest, window_start, count) VALUES (%s, %s, %s, 1) "
            f"ON CONFLICT (scope, key_digest, window_start) DO UPDATE SET count = {table}.count + 1 "
            "RETURNING count",
            # Formatted as the ORM stores it, so limit_reached() and pruning find the row on SQLite too.
            [scope, digest, connection.ops.adapt_datetimefield_value(window_start)],
        )
        return cursor.fetchone()[0]


def rate_limited(scope, key, *, now=None):
    """Count a request and report whether the scope's limit is exceeded."""
    return count_request(scope, key, now=now) > settings.ACCOUNT_RATE_LIMITS[scope][0]


def limit_reached(scope, key, *, now=None):
    """Report whether the scope's limit is used up, without counting this request."""
    limit, window_start, digest = _counter(scope, key, now)
    count = RateLimitCounter.objects.filter(scope=scope, key_digest=digest, window_start=window_start) \
        .values_list("count", flat=True).first()
    return (count or 0) >= limit


def clear_rate_limits(scopes, key):
    """Delete every window of these scopes' counters for one key."""
    RateLimitCounter.objects.filter(scope__in=scopes, key_digest=_key_digest(key)).delete()


def prune_rate_limits(*, now=None):
    """Delete counters whose window has ended in every scope; returns how many were removed."""
    longest = max(window for _, window in settings.ACCOUNT_RATE_LIMITS.values())
    count, _ = RateLimitCounter.objects.filter(
        window_start__lte=(now or timezone.now()) - timedelta(seconds=longest)).delete()
    return count


def _link(route, employee, tokens):
    uidb64 = urlsafe_base64_encode(force_bytes(employee.pk))
    return settings.APP_BASE_URL + reverse(route, args=[uidb64, tokens.make_token(employee)])


ACCOUNT_EMAILS = {
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
    subject, route, tokens = ACCOUNT_EMAILS[purpose]

    def send():
        # One send per employee at a time. The new generation is committed before the email leaves, so a
        # delivered link survives a crash; a definite failure then restores exactly the previous one.
        with _account_email_lock(employee.pk):
            issued = None
            sending = False
            try:
                with transaction.atomic():
                    current = Employee.objects.select_for_update().filter(pk=employee.pk).first()
                    if current is None:
                        return  # The registration was removed meanwhile, so there's nobody to email.
                    previous, previous_sent = current.link_generation, current.verification_sent_at
                    if route:
                        current.link_generation = previous + 1
                        fields = ["link_generation"]
                        if purpose == "verify":
                            # Only confirmation emails restart an unconfirmed registration's expiry.
                            current.verification_sent_at = timezone.now()
                            fields.append("verification_sent_at")
                        current.save(update_fields=fields)
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
                        # Certainly not delivered: the earlier link and expiry time apply again.
                        restored = {"link_generation": previous}
                        if purpose == "verify":
                            restored["verification_sent_at"] = previous_sent
                        newest.update(**restored, link_send_failed_at=timezone.now())
                    else:
                        # Ambiguous or unknown: the email may have arrived, so its link must keep working.
                        newest.update(link_send_failed_at=timezone.now())
                generation = Employee.objects.filter(pk=employee.pk).values_list("link_generation", flat=True).first()
                if generation is not None:
                    employee.link_generation = generation
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
    """Verified registrations waiting for a decision, longest-waiting first."""
    return with_verification(Employee.objects.filter(
        is_demo=False, approval_status=Employee.Approval.PENDING,
    )).filter(VERIFIED).order_by("created_at", "id")


def with_verification(queryset):
    """Adds the lowercased emails that VERIFIED compares, matching Employee.email_is_verified in SQL."""
    return queryset.alias(verified_email_lower=Lower("verified_email"), email_lower=Lower("email"))


VERIFIED = ~Q(verified_email="") & Q(verified_email_lower=F("email_lower"))


def awaiting_verification():
    """Registrations that haven't confirmed their email or been decided, so an administrator may remove them."""
    return with_verification(Employee.objects.filter(
        is_demo=False, is_active=True, approval_status=Employee.Approval.PENDING,
        approval_decided_at__isnull=True, approval_decided_by__isnull=True,
    )).exclude(VERIFIED).order_by("created_at", "id")


def real_administrator_exists():
    return Employee.objects.filter(role=Employee.Role.ADMINISTRATOR, is_demo=False).exists()


def awaiting_setup():
    return Employee.objects.filter(
        is_demo=False, approval_status=Employee.Approval.APPROVED, is_active=True,
        password__startswith=UNUSABLE_PASSWORD_PREFIX,
    ).order_by("created_at", "id")


def decided_accounts():
    """Real employees with an approval decision, newest first. An invitation counts as an approval."""
    return Employee.objects.filter(is_demo=False, approval_decided_at__isnull=False).select_related(
        "approval_decided_by").order_by("-approval_decided_at", "-id")


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


def resend_verification_for(*, actor, employee_id):
    """Send an applicant a new verify link. Shares the applicant's own resend limit, so the two can't add up."""
    _require_administrator(actor)
    try:
        employee = awaiting_verification().filter(pk=employee_id).first()
    except (ValidationError, ValueError):
        employee = None
    if employee is None:
        raise AccountInputError("This applicant has already confirmed their email address.")
    if rate_limited("account_email", f"verify:{employee.pk}"):
        raise AccountInputError("A link was sent to this applicant recently. Try again later.")
    send_account_email(employee, "verify")
    return employee


def registration_digest(email):
    """A keyed digest of an email, so a removed registration can be matched to an address without storing it."""
    return salted_hmac("training.registration_removed", normalize_email(email), algorithm="sha256").hexdigest()


def _delete_registration(employee_id, *, kind, actor=None, sent_before=None, now=None):
    """Delete an unconfirmed registration and record it. False when it's gone or no longer removable.

    Waits for any account email being sent to it, then rechecks under the row lock, so a confirmation,
    a decision or a resend that happens first always wins. Raises ProtectedError if records refer to it.
    """
    # The canonical spelling, so the lock key matches the one send_account_email takes for this employee.
    employee_id = uuid.UUID(str(employee_id))
    with _account_email_lock(employee_id), transaction.atomic():
        employee = Employee.objects.select_for_update().filter(pk=employee_id).first()
        if employee is None or not awaiting_verification().filter(pk=employee.pk).exists():
            return False
        if sent_before is not None and (employee.verification_sent_at or employee.created_at) > sent_before:
            return False
        detail = (f"Registration {employee.pk} from {employee.created_at:%Y-%m-%d}; "
                  f"email digest {registration_digest(employee.email)}")
        employee.delete()
        SecurityEvent.objects.create(kind=kind, actor=actor, detail=detail, occurred_at=now or timezone.now())
        return True


def remove_registration(*, actor, employee_id):
    _require_administrator(actor)
    try:
        removed = _delete_registration(employee_id, kind=SecurityEvent.Kind.REGISTRATION_REMOVED, actor=actor)
    except (ValidationError, ValueError):
        removed = False
    except ProtectedError:
        raise AccountInputError("Other records refer to this registration, so it can't be removed.") from None
    if not removed:
        raise AccountInputError("This registration has already been confirmed or removed.")


def expire_registrations(*, now=None, batch=100):
    """Remove up to batch registrations still unconfirmed a set time after their newest confirmation email.

    Registrations a security event refers to (the one record that can) are left out, so they can't fill
    every batch and hold back the rest. Any other reference is logged and skipped within the batch.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(days=settings.UNCONFIRMED_REGISTRATION_DAYS)
    candidates = (awaiting_verification().alias(sent=Coalesce("verification_sent_at", "created_at"))
                  .filter(sent__lte=cutoff, security_events__isnull=True).values_list("pk", flat=True)[:batch])
    removed = 0
    for employee_id in list(candidates):
        try:
            removed += _delete_registration(employee_id, kind=SecurityEvent.Kind.REGISTRATION_EXPIRED,
                                            sent_before=cutoff, now=now)
        except ProtectedError:
            logger.warning("Kept expired registration %s because other records refer to it", employee_id)
    return removed


def resend_setup(*, actor, employee_id):
    _require_administrator(actor)
    try:
        employee = awaiting_setup().get(pk=employee_id)
    except (Employee.DoesNotExist, ValidationError, ValueError):
        raise AccountInputError("This employee has already set up their account.") from None
    send_account_email(employee, "setup")
    return employee
