from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import SESSION_KEY, logout
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect

from .accounts import AccountState, account_state, is_account_administrator
from .models import Employee
from .population import is_administrator, route_in_population

# Set at sign-in and cleared at sign-out. Django flushes a session whose hash no longer matches before any view
# runs, so this cookie is the evidence that a real session existed and must not fall back to a demo profile.
REAL_SESSION_COOKIE = "real_session"


def mark_real_session(response):
    response.set_cookie(REAL_SESSION_COOKIE, "1", max_age=settings.SESSION_COOKIE_AGE, httponly=True,
                        secure=settings.SESSION_COOKIE_SECURE, samesite="Lax")
    return response


def clear_real_session(response):
    response.delete_cookie(REAL_SESSION_COOKIE, samesite="Lax")
    return response


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


def actor_required(view):
    """Require an active signed-in employee, or a demo employee on loopback while the stub is enabled."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        employee = signed_in_employee(request)
        if employee is not None:
            if account_state(employee) != AccountState.ACTIVE:
                return redirect("account_status")
            request.employee = employee
            return view(request, *args, **kwargs)
        if SESSION_KEY in request.session or request.COOKIES.get(REAL_SESSION_COOKIE):
            # A real session that no longer authenticates must never fall back to a demo profile. The marker
            # stays until the next sign-in or an explicit demo profile choice.
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


def admin_required(view):
    """Require an administrator; each works within their own population (training/population.py)."""
    @actor_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not is_administrator(request.employee):
            return HttpResponseForbidden("Administrator access is unavailable.")
        if not route_in_population(request.employee, kwargs):
            raise Http404
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
