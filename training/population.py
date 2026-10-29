"""Demo and real administrators work in separate populations; see docs/plans/31-real-employee-workflows.md.

Each helper takes the acting employee, or a bool ``is_demo`` where no actor exists.
"""
from django.db.models import Q

from .accounts import AccountState, account_state
from .models import Assignment, Employee, EmployeeGroup, ImportBatch, Notification, Quiz, Training


def _is_demo(who):
    return who if isinstance(who, bool) else who.is_demo


def employees(who):
    """Every employee an administrator may see, active or not. Pending and rejected applicants are not employees yet."""
    queryset = Employee.objects.filter(is_demo=_is_demo(who))
    if not _is_demo(who):
        queryset = queryset.filter(approval_status=Employee.Approval.APPROVED)
    return queryset


def assignable(who):
    """Employees who may receive new work. An invitation implies approval, so setup isn't required."""
    return employees(who).filter(is_active=True)


def is_assignable(employee, who):
    return assignable(who).filter(pk=employee.pk).exists()


def trainings(who):
    return Training.objects.filter(is_demo=_is_demo(who))


def library_quizzes(who):
    return Quiz.objects.filter(version__isnull=True, is_demo=_is_demo(who))


def groups(who):
    return EmployeeGroup.objects.filter(is_demo=_is_demo(who))


def is_administrator(actor):
    """An active administrator; a real one must also have an active account."""
    if actor.role != Employee.Role.ADMINISTRATOR or not actor.is_active:
        return False
    return actor.is_demo or account_state(actor) == AccountState.ACTIVE


# URL keyword arguments an administrator route may carry, and how each one is scoped.
_ROUTE_SCOPES = {
    "training_id": lambda flag: Training.objects.filter(is_demo=flag),
    "quiz_id": lambda flag: Quiz.objects.filter(
        Q(version__isnull=True, is_demo=flag) | Q(version__training__is_demo=flag)),
    "group_id": lambda flag: EmployeeGroup.objects.filter(is_demo=flag),
    "employee_id": employees,
    "assignment_id": lambda flag: Assignment.objects.filter(employee__is_demo=flag),
    "notification_id": lambda flag: Notification.objects.filter(assignment__employee__is_demo=flag),
    "batch_id": lambda flag: ImportBatch.objects.filter(owner__is_demo=flag),
}


def route_in_population(actor, kwargs):
    """Whether every object named in an administrator route belongs to the actor's population."""
    return all(_ROUTE_SCOPES[name](actor.is_demo).filter(pk=value).exists()
               for name, value in kwargs.items() if name in _ROUTE_SCOPES)
