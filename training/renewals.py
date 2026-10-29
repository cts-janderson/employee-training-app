import calendar
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction

from .employees import is_administrator
from .models import Assignment, Ending, Training


@dataclass(frozen=True)
class RenewalCandidate:
    employee: object
    completed_assignment: Assignment
    renewal_date: date


def renewal_date(assignment):
    months = assignment.training.renewal_interval_months
    if not months or assignment.ending != Ending.COMPLETED:
        return None
    completed = assignment.completed_at.astimezone(ZoneInfo(settings.ORG_TIME_ZONE)).date()
    month_index = completed.year * 12 + completed.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(completed.day, calendar.monthrange(year, month)[1]))


def _due_for_renewal(assignments, *, as_of):
    """Find each employee's due completion, including one blocked by manual assignment."""
    latest = {}
    previous = {}
    for assignment in assignments:
        if assignment.ending == Ending.CANCELED:
            continue
        previous[assignment.employee_id] = latest.get(assignment.employee_id)
        latest[assignment.employee_id] = assignment
    candidates = []
    for assignment in latest.values():
        source = assignment
        if assignment.ending is None and assignment.renews_id is None:
            source = previous.get(assignment.employee_id)
        if source is None or source.archived_at or source.ending != Ending.COMPLETED:
            continue
        due = renewal_date(source)
        if due and due <= as_of:
            candidates.append(RenewalCandidate(assignment.employee, source, due))
    return sorted(candidates, key=lambda item: (item.employee.name, item.employee.id))


def renewal_candidates(training, *, as_of, employee_ids=None, lock=False):
    """Find renewal recipients; a locking caller holds employee rows in primary-key order."""
    if not training.renewal_interval_months:
        return []

    def read():
        # A training's renewals stay within its own population.
        assignments = Assignment.objects.filter(training=training, employee__is_demo=training.is_demo)
        if employee_ids is not None:
            assignments = assignments.filter(employee_id__in=employee_ids)
        return list(assignments.select_related("employee", "training")
                    .order_by("employee_id", "assigned_at", "id"))

    candidates = _due_for_renewal(read(), as_of=as_of)
    if not lock or not candidates:
        return candidates
    source_ids = [candidate.completed_assignment.id for candidate in candidates]
    list(Assignment.objects.select_for_update().select_related("training")
         .filter(pk__in=source_ids).order_by("pk"))
    current = {candidate.employee.id: candidate for candidate in _due_for_renewal(read(), as_of=as_of)}
    return [candidate for candidate in candidates
            if candidate.employee.id in current
            and current[candidate.employee.id].completed_assignment.id == candidate.completed_assignment.id]


def set_renewal_interval(*, training_id, months, actor):
    if not is_administrator(actor):
        raise ValueError("Administrator access is required.")
    if months == "":
        value = None
    else:
        try:
            value = int(months)
        except (ValueError, TypeError):
            raise ValueError("Enter a renewal interval from 1 to 120 months.") from None
        if value < 1 or value > 120:
            raise ValueError("Enter a renewal interval from 1 to 120 months.")
    with transaction.atomic():
        training = Training.objects.select_for_update().get(pk=training_id)
        training.renewal_interval_months = value
        training.save(update_fields=["renewal_interval_months"])
    return training
