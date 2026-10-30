"""Endpoints for adding passkeys and signing in with one; see docs/plans/33-sign-in-attack-protection.md."""
import json

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import passkeys
from .accounts import rate_limited
from .dev_auth import mark_real_session
from .passkeys import PasskeyError
from .sign_in_protection import client_network, set_trusted_device

BACKEND = "training.accounts.EmployeeBackend"


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _options(text):
    return JsonResponse(json.loads(text))


def _credential(request):
    try:
        return json.loads(request.body).get("credential")
    except (ValueError, AttributeError):
        return None


@login_required
@require_POST
def register_options(request):
    """Check the re-entered password, then return creation options."""
    try:
        passkeys.reauthenticate(request, request.user, request.POST.get("password", ""))
        return _options(passkeys.registration_options(request, request.user))
    except PasskeyError as exc:
        return _error(str(exc))


@login_required
@require_POST
def register_verify(request):
    try:
        body = json.loads(request.body)
        passkey = passkeys.register(request, request.user, body.get("credential"), body.get("name"))
    except (ValueError, AttributeError):
        return _error("The passkey response was incomplete.")
    except PasskeyError as exc:
        return _error(str(exc))
    return JsonResponse({"name": passkey.name})


@login_required
@require_POST
def remove(request, passkey_id):
    try:
        passkeys.reauthenticate(request, request.user, request.POST.get("password", ""))
        passkeys.remove(request, request.user, passkey_id)
        request.session["passkey_notice"] = "Passkey removed."
    except PasskeyError as exc:
        request.session["passkey_notice"] = str(exc)
    return redirect("account_status")


@require_POST
def sign_in_options(request):
    # Address blocks don't apply here: a passkey is how an administrator gets past one.
    if rate_limited("passkey_options", client_network(request)):
        return _error("Too many requests. Wait a while and try again.", status=429)
    return _options(passkeys.authentication_options(request))


@require_POST
def sign_in_verify(request):
    try:
        employee = passkeys.authenticate(request, _credential(request))
    except PasskeyError as exc:
        return _error(str(exc))
    login(request, employee, backend=BACKEND)
    # A real session replaces any demo choice.
    request.session.pop("demo_employee_id", None)
    target = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        target = resolve_url(settings.LOGIN_REDIRECT_URL)
    response = JsonResponse({"redirect": target})
    return set_trusted_device(request, mark_real_session(response), employee)
