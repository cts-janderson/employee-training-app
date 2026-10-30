"""Administrator passkeys (WebAuthn); see docs/plans/33-sign-in-attack-protection.md section D."""
import json
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, parse_authentication_credential_json, parse_registration_credential_json
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidJSONStructure, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .accounts import is_account_administrator, limit_reached, rate_limited
from .models import Employee, Passkey, PasskeyChallenge, SecurityEvent
from .sign_in_protection import record_event

RP_NAME = "Employee Training"
CHALLENGE_MINUTES = 5
REAUTHENTICATION_MINUTES = 5
_REAUTHENTICATED_AT = "passkey_reauthenticated_at"


class PasskeyError(Exception):
    """A passkey ceremony was refused. The message is safe to show."""


def relying_party():
    """The relying party ID (the host of APP_BASE_URL) and the exact origin a response must come from."""
    parts = urlsplit(settings.APP_BASE_URL)
    return parts.hostname, f"{parts.scheme}://{parts.netloc}"


# Challenges

def _session_key(request):
    # An anonymous sign-in needs a saved session to bind its challenge to.
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def _issue_challenge(request, purpose, employee=None):
    challenge = secrets.token_bytes(32)
    PasskeyChallenge.objects.create(challenge=challenge, purpose=purpose, session_key=_session_key(request),
                                    employee=employee,
                                    expires_at=timezone.now() + timedelta(minutes=CHALLENGE_MINUTES))
    return challenge


def _use_challenge(request, credential, purpose, employee=None):
    """Use up the challenge the response signed, once, for this session and purpose. Returns its bytes."""
    try:
        challenge = base64url_to_bytes(json.loads(credential.response.client_data_json)["challenge"])
    except (KeyError, TypeError, ValueError):
        raise PasskeyError("The passkey response was incomplete.")
    now = timezone.now()
    used = PasskeyChallenge.objects.filter(
        challenge=challenge, purpose=purpose, session_key=request.session.session_key or "", employee=employee,
        used_at__isnull=True, expires_at__gt=now,
    ).update(used_at=now)
    if used != 1:
        raise PasskeyError("This passkey request has expired. Try again.")
    return challenge


def prune_challenges(*, now=None):
    count, _ = PasskeyChallenge.objects.filter(expires_at__lte=now or timezone.now()).delete()
    return count


# Re-entering the password

def reauthenticate(request, employee, password):
    """Check the current password before a passkey is added or removed, with its own failure limit."""
    key = str(employee.pk)
    if limit_reached("reauthenticate", key):
        raise PasskeyError("Too many wrong passwords. Wait a while and try again.")
    if not password or not employee.check_password(password):
        rate_limited("reauthenticate", key)
        raise PasskeyError("That password isn't correct.")
    request.session[_REAUTHENTICATED_AT] = timezone.now().timestamp()


def _recently_reauthenticated(request):
    at = request.session.get(_REAUTHENTICATED_AT)
    return at is not None and timezone.now().timestamp() - at < REAUTHENTICATION_MINUTES * 60


def _require_administrator(employee):
    if not is_account_administrator(employee):
        raise PasskeyError("Only administrators can use passkeys.")


# Adding a passkey

def registration_options(request, employee):
    """Creation options for a new discoverable, user-verified passkey, as JSON."""
    _require_administrator(employee)
    if not _recently_reauthenticated(request):
        raise PasskeyError("Enter your password again.")
    if employee.webauthn_user_handle is None:
        Employee.objects.filter(pk=employee.pk, webauthn_user_handle__isnull=True) \
            .update(webauthn_user_handle=secrets.token_bytes(32))
        employee.refresh_from_db(fields=["webauthn_user_handle"])
    rp_id, _ = relying_party()
    options = generate_registration_options(
        rp_id=rp_id, rp_name=RP_NAME, user_id=bytes(employee.webauthn_user_handle), user_name=employee.email,
        user_display_name=employee.name,
        challenge=_issue_challenge(request, PasskeyChallenge.Purpose.REGISTER, employee),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=bytes(passkey.credential_id))
                             for passkey in employee.passkeys.all()],
    )
    return options_to_json(options)


def register(request, employee, credential_json, name):
    """Verify a new passkey and store it. Returns the Passkey."""
    _require_administrator(employee)
    if not _recently_reauthenticated(request):
        raise PasskeyError("Enter your password again.")
    name = (name or "").strip()[:100] or "Passkey"
    try:
        credential = parse_registration_credential_json(credential_json)
    except (InvalidJSONStructure, InvalidRegistrationResponse, ValueError, TypeError):
        raise PasskeyError("The passkey response was incomplete.")
    rp_id, origin = relying_party()
    with transaction.atomic():
        challenge = _use_challenge(request, credential, PasskeyChallenge.Purpose.REGISTER, employee)
        try:
            verified = verify_registration_response(credential=credential, expected_challenge=challenge,
                                                    expected_rp_id=rp_id, expected_origin=origin,
                                                    require_user_verification=True)
        except InvalidRegistrationResponse:
            raise PasskeyError("The passkey couldn't be verified.")
        if Passkey.objects.filter(credential_id=verified.credential_id).exists():
            raise PasskeyError("This passkey is already added.")
        passkey = Passkey.objects.create(
            employee=employee, credential_id=verified.credential_id, public_key=verified.credential_public_key,
            sign_count=verified.sign_count, transports=[str(t.value) for t in credential.response.transports or []],
            name=name, created_at=timezone.now())
        record_event(SecurityEvent.Kind.PASSKEY_ADDED, employee=employee, actor=employee, detail=name)
    return passkey


def remove(request, employee, passkey_id):
    """Remove one of the administrator's own passkeys after a recent password re-entry."""
    if not _recently_reauthenticated(request):
        raise PasskeyError("Enter your password again.")
    with transaction.atomic():
        passkey = Passkey.objects.select_for_update().filter(pk=passkey_id, employee=employee).first()
        if passkey is None:
            raise PasskeyError("That passkey has already been removed.")
        passkey.delete()
        record_event(SecurityEvent.Kind.PASSKEY_REMOVED, employee=employee, actor=employee, detail=passkey.name)


def remove_all(employee):
    """Remove every passkey an employee has, from the host. Returns how many were removed."""
    with transaction.atomic():
        count, _ = Passkey.objects.filter(employee=employee).delete()
        record_event(SecurityEvent.Kind.PASSKEYS_COMMAND, employee=employee, detail=f"{count} removed")
    return count


# Signing in with a passkey

def authentication_options(request):
    """Request options that let the browser offer any discoverable passkey for this site, as JSON."""
    rp_id, _ = relying_party()
    options = generate_authentication_options(
        rp_id=rp_id, challenge=_issue_challenge(request, PasskeyChallenge.Purpose.AUTHENTICATE),
        user_verification=UserVerificationRequirement.REQUIRED)
    return options_to_json(options)


def authenticate(request, credential_json):
    """Verify a passkey sign-in and return its administrator. Failures count against the passkey."""
    try:
        credential = parse_authentication_credential_json(credential_json)
    except (InvalidJSONStructure, InvalidAuthenticationResponse, ValueError, TypeError):
        raise PasskeyError("The passkey response was incomplete.")
    failure_key = credential.raw_id.hex()
    if limit_reached("passkey_failures", failure_key):
        raise PasskeyError("Too many failed passkey sign-ins. Wait a while and try again.")
    rp_id, origin = relying_party()
    try:
        with transaction.atomic():
            challenge = _use_challenge(request, credential, PasskeyChallenge.Purpose.AUTHENTICATE)
            passkey = Passkey.objects.select_for_update().select_related("employee") \
                .filter(credential_id=credential.raw_id).first()
            if passkey is None:
                raise PasskeyError("This passkey isn't recognized.")
            employee = passkey.employee
            # The verifier doesn't check whose credential this is; the stored owner must match the response.
            if credential.response.user_handle is None or \
                    bytes(credential.response.user_handle) != bytes(employee.webauthn_user_handle or b""):
                raise PasskeyError("This passkey isn't recognized.")
            _require_administrator(employee)
            try:
                verified = verify_authentication_response(
                    credential=credential, expected_challenge=challenge, expected_rp_id=rp_id,
                    expected_origin=origin, credential_public_key=bytes(passkey.public_key),
                    credential_current_sign_count=passkey.sign_count, require_user_verification=True)
            except InvalidAuthenticationResponse:
                raise PasskeyError("The passkey couldn't be verified.")
            passkey.sign_count, passkey.last_used_at = verified.new_sign_count, timezone.now()
            passkey.save(update_fields=["sign_count", "last_used_at"])
    except PasskeyError:
        rate_limited("passkey_failures", failure_key)
        raise
    return employee
