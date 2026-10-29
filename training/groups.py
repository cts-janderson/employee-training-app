import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .employees import is_administrator
from .models import Employee, EmployeeGroup, EmployeeGroupChange, EmployeeGroupMembership
from .population import assignable, groups as groups_in


class GroupInputError(ValueError):
    def __init__(self, message, target=None):
        super().__init__(message)
        self.target = target


def _require_admin(actor):
    if not is_administrator(actor):
        raise GroupInputError("Administrator access is required.")


def _name(value):
    name = value.strip() if isinstance(value, str) else ""
    if not name or len(name) > 200:
        raise GroupInputError("Enter a group name of at most 200 characters.", target=("name",))
    return name


def _locked_group(actor, group_id):
    try:
        return groups_in(actor).select_for_update().get(pk=group_id)
    except (EmployeeGroup.DoesNotExist, ValidationError, ValueError):
        raise GroupInputError("Choose a valid employee group.") from None


def create_group(*, name, actor, now=None):
    _require_admin(actor)
    name = _name(name)
    now = now or timezone.now()
    try:
        with transaction.atomic():
            group = EmployeeGroup.objects.create(name=name, is_demo=actor.is_demo, created_at=now)
            EmployeeGroupChange.objects.create(group=group, actor=actor, kind=EmployeeGroupChange.Kind.CREATED,
                                               new_name=name, occurred_at=now)
            return group
    except IntegrityError:
        raise GroupInputError("A group already uses that name.", target=("name",)) from None


def rename_group(*, group_id, name, actor, now=None):
    _require_admin(actor)
    name = _name(name)
    now = now or timezone.now()
    try:
        with transaction.atomic():
            group = _locked_group(actor, group_id)
            if group.name != name:
                old_name = group.name
                group.name = name
                group.save(update_fields=["name"])
                EmployeeGroupChange.objects.create(group=group, actor=actor, kind=EmployeeGroupChange.Kind.RENAMED,
                                                   old_name=old_name, new_name=name, occurred_at=now)
            return group
    except IntegrityError:
        raise GroupInputError("A group already uses that name.", target=("name",)) from None


def set_group_members(*, group_id, employee_ids, actor, now=None):
    _require_admin(actor)
    now = now or timezone.now()
    try:
        ids = {uuid.UUID(str(value)) for value in employee_ids}
    except (ValueError, TypeError):
        raise GroupInputError("Choose valid employees.", target=("members",)) from None
    with transaction.atomic():
        group = _locked_group(actor, group_id)
        current = {item.employee_id: item for item in group.memberships.select_related("employee")}
        additions = ids - current.keys()
        eligible = list(assignable(actor).select_for_update().filter(pk__in=additions).order_by("pk"))
        if len(eligible) != len(additions):
            raise GroupInputError("Only active employees can be added.", target=("members",))
        for item in list(current.values()):
            if item.employee_id not in ids:
                item.delete()
                EmployeeGroupChange.objects.create(group=group, actor=actor,
                                                   kind=EmployeeGroupChange.Kind.MEMBER_REMOVED,
                                                   employee=item.employee, occurred_at=now)
        for employee in eligible:
            EmployeeGroupMembership.objects.create(group=group, employee=employee, added_at=now)
            EmployeeGroupChange.objects.create(group=group, actor=actor,
                                               kind=EmployeeGroupChange.Kind.MEMBER_ADDED,
                                               employee=employee, occurred_at=now)
    return group


def group_members(group):
    return list(Employee.objects.filter(group_memberships__group=group).order_by("name", "id"))
