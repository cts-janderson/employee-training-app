from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from .accounts import AccountInputError, AccountState, account_state, normalize_email, send_account_email
from .accounts import _validated_profile as _validated_account_profile
from .models import Employee, EmployeeActivityChange, EmployeeProfileChange
from .population import employees as employees_in, is_administrator


class EmployeeInputError(Exception):
    def __init__(self, message, *, target=None):
        super().__init__(message)
        self.target = target


def employee_status(employee):
    """The directory label, badge tone and explanation for an employee."""
    if not employee.is_active:
        return "Inactive", "gray", ""
    if employee.is_demo:
        if not employee.email_is_verified:
            return "Email unverified", "amber", "Email updates are unavailable for this address."
        return "Active", "success", ""
    if not employee.has_usable_password():
        return "Awaiting setup", "amber", "A setup link was sent. They can be assigned training before they finish."
    if not employee.email_is_verified:
        return ("Email unverified", "amber",
                "A verification link was sent. Email updates resume once they confirm this address.")
    return "Active", "success", ""


def _validated_profile(name, email):
    name = name.strip()
    email = normalize_email(email)
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


def _validated_real_profile(name, email):
    try:
        return _validated_account_profile(name, email)
    except AccountInputError as exc:
        raise EmployeeInputError(str(exc), target=("profile", exc.field)) from None


def _lock_real_administrators():
    """Lock real administrators in primary-key order, before any other employee row, so edits can't deadlock."""
    return list(Employee.objects.select_for_update().filter(
        is_demo=False, role=Employee.Role.ADMINISTRATOR).order_by("pk"))


def _is_last_usable_administrator(employee, administrators):
    usable = [item.pk for item in administrators
              if account_state(item) == AccountState.ACTIVE and item.has_usable_password()]
    return usable == [employee.pk]


def update_employee(*, employee_id, name, email, active, actor, now=None):
    """Edit an employee in the actor's population. A real address change needs verification again."""
    if not is_administrator(actor):
        raise EmployeeInputError("Administrator access is unavailable.")
    name, email = (_validated_profile if actor.is_demo else _validated_real_profile)(name, email)
    now = now or timezone.now()
    with transaction.atomic():
        administrators = [] if actor.is_demo else _lock_real_administrators()
        try:
            employee = employees_in(actor).select_for_update().get(pk=employee_id)
        except (Employee.DoesNotExist, ValidationError, ValueError):
            raise EmployeeInputError("This employee no longer exists.") from None
        if employee.pk == actor.pk and not active:
            raise EmployeeInputError("Switch to another administrator before deactivating this account.",
                                     target=("profile", "active"))
        old_name, old_email, was_active = employee.name, employee.email, employee.is_active
        email_changed = old_email != email
        deactivating = was_active and not active
        if not employee.is_demo and (email_changed or deactivating) and _is_last_usable_administrator(employee, administrators):
            raise EmployeeInputError("Keep at least one administrator who can sign in.",
                                     target=("profile", "email" if email_changed else "active"))
        employee.name = name
        employee.email = email
        employee.is_active = active
        fields = ["name", "email", "is_active"]
        if employee.is_demo:
            employee.verified_email = email
            fields.append("verified_email")
        elif email_changed:
            # Earlier links stop working; the new address must prove mailbox control.
            employee.verified_email = ""
            employee.email_verified_at = None
            employee.link_generation += 1
            fields += ["verified_email", "email_verified_at", "link_generation"]
        if deactivating:
            # Sessions and links from before deactivation stay dead after any reactivation.
            employee.session_version += 1
            employee.link_generation += 1
            fields += ["session_version", "link_generation"]
        employee.save(update_fields=list(dict.fromkeys(fields)))
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
        if not employee.is_demo and email_changed and active:
            send_account_email(employee, "verify" if employee.has_usable_password() else "setup")
        return employee
