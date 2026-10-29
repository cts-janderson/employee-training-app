from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .dev_auth import admin_required, clear_real_session, demo_environment_required
from .browsing import browse, safe_return_to
from .accounts import AccountInputError, invite_employee
from .employees import EmployeeInputError, create_demo_employee, employee_status, update_employee
from .models import Employee
from .population import employees as employees_in
from .services import Problem


def _profile_field_error(problem):
    return problem.target[1] if problem.target else "email"


@require_http_methods(["GET", "POST"])
@admin_required
def employee_directory(request):
    error = None
    form_name = request.POST.get("name", "") if request.method == "POST" else ""
    form_email = request.POST.get("email", "") if request.method == "POST" else ""
    if request.method == "POST":
        name, email = request.POST.get("name", ""), request.POST.get("email", "")
        try:
            if request.employee.is_demo:
                create_demo_employee(name=name, email=email)
            else:
                # A real employee is invited and chooses their own password through a setup link.
                invite_employee(actor=request.employee, name=name, email=email)
            return redirect("employee_directory")
        except AccountInputError as exc:
            error = Problem(str(exc), ("profile", exc.field) if exc.field else None)
        except (EmployeeInputError, IntegrityError) as exc:
            error = (Problem("An employee already uses that email.", ("profile", "email"))
                     if isinstance(exc, IntegrityError) else Problem(str(exc), getattr(exc, "target", None)))
    listing = browse(request, employees_in(request.employee).order_by("name", "id"),
                     search_fields=("name", "email"))
    rows = [{"employee": employee, "status": employee_status(employee)} for employee in listing.page.object_list]
    return render(request, "training/employees.html", {
        "employees": rows, "invites": not request.employee.is_demo, "error": error, "error_field": _profile_field_error(error) if error else "",
        "form_name": form_name, "form_email": form_email,
        "browse": listing,
    }, status=400 if error else 200)


@require_http_methods(["GET", "POST"])
@admin_required
def edit_employee(request, employee_id):
    employee = get_object_or_404(employees_in(request.employee), pk=employee_id)
    error = None
    form_name = request.POST.get("name", "") if request.method == "POST" else employee.name
    form_email = request.POST.get("email", "") if request.method == "POST" else employee.email
    form_active = request.POST.get("active") == "on" if request.method == "POST" else employee.is_active
    if request.method == "POST":
        try:
            update_employee(
                employee_id=employee.id,
                name=request.POST.get("name", ""),
                email=request.POST.get("email", ""),
                active=request.POST.get("active") == "on",
                actor=request.employee,
            )
            return redirect("edit_employee", employee_id=employee.id)
        except (EmployeeInputError, IntegrityError) as exc:
            error = (Problem("An employee already uses that email.", ("profile", "email"))
                     if isinstance(exc, IntegrityError) else Problem(str(exc), getattr(exc, "target", None)))
    employee.refresh_from_db()
    changes = employee.profile_changes.select_related("actor").order_by("-changed_at")
    activity_changes = employee.activity_changes.select_related("actor").order_by("-changed_at")
    return render(request, "training/employee_edit.html", {
        "employee": employee, "status": employee_status(employee), "changes": changes, "activity_changes": activity_changes, "error": error,
        "error_field": _profile_field_error(error) if error else "",
        "form_name": form_name, "form_email": form_email, "form_active": form_active,
        "back_url": safe_return_to(request, "employee_directory"),
    }, status=400 if error else 200)


@require_http_methods(["GET", "POST"])
@demo_environment_required
def demo_switch(request):
    if request.user.is_authenticated:
        return HttpResponseForbidden("Sign out before switching to a demo profile.")
    if request.method == "POST":
        try:
            employee = get_object_or_404(Employee, pk=request.POST.get("employee_id"), is_demo=True)
        except (ValidationError, ValueError):
            return HttpResponseBadRequest("Choose a valid demo employee.")
        if not employee.is_active:
            return HttpResponseForbidden("An inactive employee cannot sign in.")
        request.session["demo_employee_id"] = str(employee.id)
        # Choosing a demo profile is an explicit decision to leave any ended real session behind.
        return clear_real_session(redirect("my_training"))
    employees = Employee.objects.filter(is_demo=True, is_active=True).order_by("name")
    return render(request, "training/demo_switch.html", {"employees": employees})
