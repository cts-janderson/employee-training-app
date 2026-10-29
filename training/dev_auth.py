from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import SESSION_KEY, logout
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect

from .accounts import AccountState, account_state, is_account_administrator
from .models import Employee


def signed_in_employee(request):
    """The employee signed in with a password, or None. A real session takes precedence over the demo switcher."""
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


def demo_enabled():
    return settings.DEBUG and settings.DEV_AUTH_STUB


def _demo_environment_problem(request):
    if not demo_enabled():
        return "Sign-in is not connected yet."
    if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        return "Demo access is available only on loopback."
    if urlsplit("//" + request.get_host()).hostname not in {"localhost", "127.0.0.1", "::1"}:
        return "Demo access is available only on loopback."
    return None


def demo_environment_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        problem = _demo_environment_problem(request)
        if problem:
            return HttpResponseForbidden(problem)
        return view(request, *args, **kwargs)

    return wrapped


def demo_actor_required(view):
    """Require an active signed-in employee, or a demo employee on loopback while the stub is enabled."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        employee = signed_in_employee(request)
        if employee is not None:
            if account_state(employee) != AccountState.ACTIVE:
                return redirect("account_status")
            request.employee = employee
            return view(request, *args, **kwargs)
        if SESSION_KEY in request.session:
            # A real session that no longer authenticates must never fall back to a demo profile.
            logout(request)
            return redirect_to_login(request.get_full_path())
        problem = _demo_environment_problem(request)
        if problem and not demo_enabled():
            return redirect_to_login(request.get_full_path())
        if problem:
            return HttpResponseForbidden(problem)
        actor_id = request.session.get("demo_employee_id")
        try:
            if actor_id:
                employee = Employee.objects.get(pk=actor_id, is_demo=True)
            else:
                employee = Employee.objects.get(email__iexact=settings.DEV_ADMIN_EMAIL, is_demo=True)
                request.session["demo_employee_id"] = str(employee.id)
        except (Employee.DoesNotExist, ValueError):
            return HttpResponse("Create or select a demo employee.", status=503)
        if not employee.is_active:
            return HttpResponseForbidden("This employee is inactive.")
        request.employee = employee
        return view(request, *args, **kwargs)

    return wrapped


def demo_admin_required(view):
    """Administrator workflows still operate on demo data only; see docs/plans/29-sign-in-skeleton.md."""
    @demo_actor_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.employee.is_demo:
            return HttpResponseForbidden(
                "This page works with demo data only. Signed-in administrators can use Accounts.")
        if request.employee.role != Employee.Role.ADMINISTRATOR:
            return HttpResponseForbidden("Administrator access is unavailable.")
        return view(request, *args, **kwargs)

    return wrapped


def account_admin_required(view):
    """Require an active, signed-in, non-demo administrator."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        employee = signed_in_employee(request)
        if employee is None:
            return redirect_to_login(request.get_full_path())
        if account_state(employee) != AccountState.ACTIVE:
            return redirect("account_status")
        if not is_account_administrator(employee):
            return HttpResponseForbidden("Administrator access is unavailable.")
        request.employee = employee
        return view(request, *args, **kwargs)

    return wrapped
