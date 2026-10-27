from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden

from .models import Employee


def demo_environment_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not settings.DEBUG or not settings.DEV_AUTH_STUB:
            return HttpResponseForbidden("Sign-in is not connected yet.")
        if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
            return HttpResponseForbidden("Demo access is available only on loopback.")
        if urlsplit("//" + request.get_host()).hostname not in {"localhost", "127.0.0.1", "::1"}:
            return HttpResponseForbidden("Demo access is available only on loopback.")
        return view(request, *args, **kwargs)

    return wrapped


def demo_actor_required(view):
    @demo_environment_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
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
    @demo_actor_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.employee.role != Employee.Role.ADMINISTRATOR:
            return HttpResponseForbidden("Administrator access is unavailable.")
        return view(request, *args, **kwargs)

    return wrapped
