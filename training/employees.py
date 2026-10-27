from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from .models import Employee, EmployeeActivityChange, EmployeeProfileChange


class EmployeeInputError(Exception):
    def __init__(self, message, *, target=None):
        super().__init__(message)
        self.target = target


def is_administrator(actor):
    return actor.role == Employee.Role.ADMINISTRATOR and actor.is_active and actor.is_demo


def _validated_profile(name, email):
    name = name.strip()
    email = email.strip().lower()
    if not name or len(name) > 200:
        raise EmployeeInputError("Enter a name of at most 200 characters.", target=("profile", "name"))
    try:
        validate_email(email)
    except ValidationError:
        raise EmployeeInputError("Enter a valid email address.", target=("profile", "email")) from None
    if not email.endswith(".test"):
        raise EmployeeInputError("Local demonstration employees must use a .test email address.",
                                 target=("profile", "email"))
    return name, email


def create_demo_employee(*, name, email):
    name, email = _validated_profile(name, email)
    with transaction.atomic():
        return Employee.objects.create(name=name, email=email, verified_email=email, is_demo=True)


def update_demo_employee(*, employee_id, name, email, active, actor, now=None):
    name, email = _validated_profile(name, email)
    now = now or timezone.now()
    with transaction.atomic():
        employee = Employee.objects.select_for_update().get(pk=employee_id, is_demo=True)
        if employee.pk == actor.pk and not active:
            raise EmployeeInputError("Switch to another administrator before deactivating this account.",
                                     target=("profile", "active"))
        old_name, old_email, was_active = employee.name, employee.email, employee.is_active
        employee.name = name
        employee.email = email
        employee.verified_email = email
        employee.is_active = active
        employee.save(update_fields=["name", "email", "verified_email", "is_active"])
        if old_name != name or old_email != email:
            EmployeeProfileChange.objects.create(
                employee=employee,
                actor=actor,
                old_name=old_name,
                new_name=name,
                old_email=old_email,
                new_email=email,
            )
        if was_active != active:
            EmployeeActivityChange.objects.create(
                employee=employee, actor=actor, was_active=was_active,
                is_active=active, changed_at=now,
            )
        return employee
