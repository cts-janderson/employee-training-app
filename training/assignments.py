import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as utc_timezone
from typing import NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .employees import is_administrator
from .notifications import assignment_created, assignment_ended, deadline_changed
from .models import (
    Assignment,
    assignment_ending,
    AssignmentHistory,
    AssignmentOperation,
    AssignmentOperationResult,
    AssignmentSlideProgress,
    Employee,
    EmployeeGroup,
    Ending,
    Quiz,
    Slide,
    TrainingVersion,
    Training,
)
from .population import employees as employees_in, groups as groups_in, trainings as trainings_in
from .renewals import renewal_candidates


class AssignmentError(Exception):
    pass


class AssignmentInputError(AssignmentError):
    pass


class AssignmentAccessError(AssignmentError):
    pass


class AssignmentConflict(AssignmentError):
    pass


class LearnerDenial:
    MISSING = "missing"
    NOT_YOURS = "not_yours"
    INACTIVE = "inactive"
    CANCELED = "canceled"
    WAIVED = "waived"


class LearnerAccessLevel:
    NONE = "none"
    REVIEW = "review"
    ACT = "act"


_DENIAL_MESSAGES = {
    LearnerDenial.NOT_YOURS: "This assignment is not available to you.",
    LearnerDenial.INACTIVE: "This assignment is not available to you.",
    LearnerDenial.CANCELED: "This assignment was canceled.",
    LearnerDenial.WAIVED: "This assignment was waived.",
}


class LearnerAccess(NamedTuple):
    """What a learner may do with an assignment; WAIVED gives review access only."""
    assignment: Assignment | None
    denial: str | None

    @property
    def level(self):
        if self.denial == LearnerDenial.WAIVED:
            return LearnerAccessLevel.REVIEW
        return LearnerAccessLevel.NONE if self.denial else LearnerAccessLevel.ACT

    @property
    def can_review(self):
        return self.level != LearnerAccessLevel.NONE

    @property
    def can_act(self):
        return self.level == LearnerAccessLevel.ACT

    @property
    def message(self):
        return _DENIAL_MESSAGES.get(self.denial)


PROGRESS_LABELS = {"not_started": "Not started", "in_progress": "In progress", "completed": "Completed"}
DEADLINE_LABELS = {"on_time": "On time", "overdue": "Overdue", "completed_late": "Completed late"}
STATE_LABELS = {"pending": "Pending", **dict(Ending.choices)}


def due_date_boundary(due_date, zone_name):
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        raise AssignmentInputError("The organization time zone is invalid.") from None
    try:
        next_day = due_date + timedelta(days=1)
    except OverflowError:
        raise AssignmentInputError("Choose an earlier due date.") from None
    return datetime.combine(next_day, time.min, tzinfo=zone).astimezone(utc_timezone.utc)


def _parse_date(value):
    try:
        due_date = date.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(due_date, date) or isinstance(due_date, datetime):
            raise ValueError
        return due_date
    except (ValueError, TypeError):
        raise AssignmentInputError("Choose a valid due date.") from None


def validate_new_due_date(value, *, now=None, zone_name=None):
    now = now or timezone.now()
    zone_name = zone_name or settings.ORG_TIME_ZONE
    due_date = _parse_date(value)
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        raise AssignmentInputError("The organization time zone is invalid.") from None
    if due_date < now.astimezone(zone).date():
        raise AssignmentInputError("Choose today or a later due date in the organization time zone.")
    return due_date, zone_name, due_date_boundary(due_date, zone_name)


def progress_status(assignment):
    if assignment.completed_at:
        return "completed"
    if assignment.started_at:
        return "in_progress"
    return "not_started"


def assignment_state(assignment):
    return assignment_ending(assignment) or "pending"


def deadline_status(assignment, *, now=None):
    now = now or timezone.now()
    if assignment.completed_at:
        return "completed_late" if assignment.completed_at >= assignment.due_at else "on_time"
    return "overdue" if now >= assignment.due_at else "on_time"


@dataclass(frozen=True)
class AssignmentStatus:
    state: str
    progress: str
    deadline: str | None
    archived: bool

    @property
    def state_label(self):
        return STATE_LABELS[self.state]

    @property
    def report_state_label(self):
        return f"Archived · {self.state_label}" if self.archived else self.state_label

    @property
    def progress_label(self):
        return PROGRESS_LABELS[self.progress]

    @property
    def deadline_label(self):
        return DEADLINE_LABELS[self.deadline] if self.deadline else "—"


def assignment_status(assignment, *, now=None):
    """The state, progress and deadline codes shown for an assignment.

    AssignmentQuerySet.with_status() mirrors these rules in SQL.
    """
    state = assignment_state(assignment)
    return AssignmentStatus(
        state=state,
        progress=progress_status(assignment),
        deadline=None if state in (Ending.CANCELED, Ending.WAIVED) else deadline_status(assignment, now=now),
        archived=assignment.archived_at is not None,
    )


def slides_acknowledged(assignment):
    # Kept as a compatibility name for quiz callers; dispatch by assigned format.
    from .learning_content import complete
    return complete(assignment)


def learner_next_step(assignment, slides, acknowledged_ids, has_quiz):
    if assignment_ending(assignment):
        return "review", None
    remaining = [slide for slide in slides if slide.id not in acknowledged_ids]
    if remaining:
        saved = next((slide for slide in remaining if slide.id == (getattr(assignment, "resume_document_id", None) or assignment.resume_slide_id)), None)
        return ("resume" if assignment.started_at else "start"), saved or remaining[0]
    if has_quiz:
        return "take_quiz", None
    return "review", None


def _fingerprint(actor_id, version_id, due_date, employee_ids=None, *, recipients=None):
    recipients = recipients or Recipients.employees(employee_ids)
    payload = [str(actor_id), str(version_id), due_date.isoformat(), *recipients.fingerprint_parts()]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


class Recipients:
    source = None

    @classmethod
    def employees(cls, ids):
        return EmployeeListRecipients(tuple(ids))

    @classmethod
    def group(cls, group_id):
        try:
            return EmployeeGroupRecipients(uuid.UUID(str(group_id)))
        except (ValueError, TypeError):
            raise AssignmentInputError("Choose a valid employee group.") from None

    @classmethod
    def renewal(cls, training_id, as_of):
        try:
            return RenewalRecipients(uuid.UUID(str(training_id)), _parse_date(as_of))
        except (ValueError, TypeError):
            raise AssignmentInputError("Choose a valid renewal training.") from None

    def parsed(self):
        return self

    def fingerprint_parts(self):
        raise NotImplementedError

    def check_before_version(self, *, is_demo):
        pass

    def check_version(self, version):
        pass

    def resolve(self, *, is_demo, lock=False):
        """Recipients in one population: the population of the training being assigned."""
        raise NotImplementedError

    def operation_fields(self):
        return {"source": self.source, "group_id": None, "renewal_as_of": None}

    def review_summary(self):
        return {"source": self.source, "group_id": None, "group_name": ""}


@dataclass(frozen=True)
class ResolvedRecipient:
    employee: Employee
    renews: Assignment | None = None
    renewal_date: date | None = None


MAX_RECIPIENTS = 100


@dataclass(frozen=True)
class EmployeeListRecipients(Recipients):
    employee_ids: tuple
    source = "employees"

    def parsed(self):
        return EmployeeListRecipients(tuple(_parse_employee_ids(self.employee_ids)))

    def fingerprint_parts(self):
        return [sorted(str(value) for value in self.employee_ids)]

    def resolve(self, *, is_demo, lock=False):
        _, ids = parse_assignment_request(uuid.uuid4(), self.employee_ids)
        return [ResolvedRecipient(employee) for employee in load_assignment_employees(ids, is_demo=is_demo, lock=lock)]


@dataclass(frozen=True)
class EmployeeGroupRecipients(Recipients):
    group_id: uuid.UUID
    source = "group"

    def fingerprint_parts(self):
        return ["group", str(self.group_id)]

    def check_before_version(self, *, is_demo):
        if not groups_in(is_demo).filter(pk=self.group_id).exists():
            raise AssignmentInputError("Choose a valid employee group.")

    def resolve(self, *, is_demo, lock=False):
        group = groups_in(is_demo)
        if lock:
            group = group.select_for_update()
        try:
            group = group.get(pk=self.group_id)
        except EmployeeGroup.DoesNotExist:
            raise AssignmentInputError("Choose a valid employee group.") from None
        ids = list(group.memberships.values_list("employee_id", flat=True))
        if not ids:
            raise AssignmentInputError("This group has no members.")
        if len(ids) > MAX_RECIPIENTS:
            raise AssignmentInputError("Choose a group with at most 100 members.")
        return [ResolvedRecipient(employee) for employee in load_assignment_employees(ids, is_demo=is_demo, lock=lock)]

    def operation_fields(self):
        return {**super().operation_fields(), "group_id": self.group_id}

    def review_summary(self):
        return {"source": self.source, "group_id": self.group_id,
                "group_name": EmployeeGroup.objects.get(pk=self.group_id).name}


@dataclass(frozen=True)
class RenewalRecipients(Recipients):
    training_id: uuid.UUID
    as_of: date
    source = "renewal"

    def fingerprint_parts(self):
        return ["renewal", str(self.training_id), self.as_of.isoformat()]

    def check_version(self, version):
        if self.training_id != version.training_id:
            raise AssignmentInputError("Choose a version of the training being renewed.")

    def resolve(self, *, is_demo, lock=False):
        try:
            training = trainings_in(is_demo).get(pk=self.training_id)
        except Training.DoesNotExist:
            raise AssignmentInputError("Choose a valid renewal training.") from None
        candidates = renewal_candidates(training, as_of=self.as_of)
        if not candidates:
            raise AssignmentInputError("No completed assignments are due for renewal on or before this date.")
        if len(candidates) > MAX_RECIPIENTS:
            raise AssignmentInputError("Choose at most 100 renewal recipients.")
        employees = load_assignment_employees([candidate.employee.id for candidate in candidates],
                                              is_demo=is_demo, lock=lock)
        if lock:
            candidates = renewal_candidates(training, as_of=self.as_of,
                                            employee_ids=[employee.id for employee in employees], lock=True)
            if not candidates:
                raise AssignmentInputError("No completed assignments are due for renewal on or before this date.")
        by_id = {candidate.employee.id: candidate for candidate in candidates}
        return [ResolvedRecipient(employee, by_id[employee.id].completed_assignment,
                                  by_id[employee.id].renewal_date)
                for employee in employees if employee.id in by_id]

    def operation_fields(self):
        return {**super().operation_fields(), "renewal_as_of": self.as_of}


def _parse_operation_id(operation_id):
    try:
        return uuid.UUID(str(operation_id))
    except ValueError:
        raise AssignmentInputError("An assignment identifier is invalid.") from None


def _parse_employee_ids(employee_ids):
    try:
        ids = [uuid.UUID(str(value)) for value in employee_ids]
    except ValueError:
        raise AssignmentInputError("An assignment identifier is invalid.") from None
    if not ids or len(ids) > MAX_RECIPIENTS or len(set(ids)) != len(ids):
        raise AssignmentInputError("Choose one to 100 distinct employees.")
    return ids


def parse_recipients_request(operation_id, recipients):
    """Return the operation ID and parsed source, preserving validation order."""
    return _parse_operation_id(operation_id), recipients.parsed()


def parse_assignment_request(operation_id, employee_ids):
    return _parse_operation_id(operation_id), _parse_employee_ids(employee_ids)


def load_assignment_employees(employee_ids, *, is_demo, lock=False):
    # Under lock, this also rechecks approval: a pending or rejected applicant is not found.
    employees = employees_in(is_demo).filter(pk__in=employee_ids)
    if lock:
        employees = employees.select_for_update().order_by("pk")
    else:
        employees = employees.order_by("name")
    employees = list(employees)
    if len(employees) != len(employee_ids):
        raise AssignmentInputError("One or more employees were not found.")
    return employees


def _plan_one(employee, current, version, *, due_date, zone_name, due_at):
    Outcome = AssignmentOperationResult.Outcome
    if not employee.is_active:
        return Outcome.INACTIVE
    if current and (current.version_id == version.id and current.due_date == due_date
                    and current.due_timezone == zone_name and current.due_at == due_at):
        return Outcome.ALREADY_ASSIGNED
    if current:
        return Outcome.CONFLICT
    return None


def plan_assignments(version, employees, *, due_date, zone_name, due_at):
    current_by_employee = {assignment.employee_id: assignment for assignment in
                           Assignment.objects.outstanding().filter(
                               employee__in=employees, training=version.training,
                           ).select_related("version")}
    return [(employee, _plan_one(employee, current_by_employee.get(employee.id), version,
                                 due_date=due_date, zone_name=zone_name, due_at=due_at),
             current_by_employee.get(employee.id)) for employee in employees]


_OUTCOME_DETAILS = {
    AssignmentOperationResult.Outcome.INACTIVE: "Employee is inactive.",
    AssignmentOperationResult.Outcome.ALREADY_ASSIGNED: "This obligation already exists.",
    AssignmentOperationResult.Outcome.CONFLICT: "Cancel the outstanding assignment before replacing it.",
    AssignmentOperationResult.Outcome.CREATED: "Assignment created.",
}


def create_assignments(*, operation_id, actor, version_id, employee_ids=None, recipients=None,
                       due_date_value, now=None):
    _administrator_can_change(actor)
    try:
        version_id = uuid.UUID(str(version_id))
    except ValueError:
        raise AssignmentInputError("An assignment identifier is invalid.") from None
    if recipients is None:
        recipients = Recipients.employees(employee_ids or ())
    if not isinstance(recipients, Recipients):
        raise AssignmentInputError("Choose valid assignment recipients.")
    operation_id, recipients = parse_recipients_request(operation_id, recipients)
    now = now or timezone.now()
    due_date = _parse_date(due_date_value)
    fingerprint = _fingerprint(actor.id, version_id, due_date, recipients=recipients)
    recipients.check_before_version(is_demo=actor.is_demo)
    with transaction.atomic():
        try:
            version = TrainingVersion.objects.select_related("training").get(pk=version_id)
        except TrainingVersion.DoesNotExist:
            raise AssignmentInputError("Choose a published training version.") from None
        if version.status != TrainingVersion.Status.PUBLISHED or version.training.is_demo != actor.is_demo:
            raise AssignmentInputError("Choose a published training version.")
        recipients.check_version(version)
        operation, created = AssignmentOperation.objects.get_or_create(
            pk=operation_id,
            defaults={"actor": actor, "version": version, "request_fingerprint": fingerprint,
                      **recipients.operation_fields()},
        )
        if not created:
            if operation.request_fingerprint != fingerprint:
                raise AssignmentInputError("This assignment operation ID was used for a different request.")
            return list(operation.results.select_related("employee", "assignment").order_by("employee__name"))

        due_date, zone_name, due_at = validate_new_due_date(due_date, now=now)

        resolved = recipients.resolve(is_demo=version.training.is_demo, lock=True)
        results = []
        for recipient in resolved:
            employee = recipient.employee
            pending = Assignment.objects.outstanding().filter(employee=employee, training=version.training).first()
            outcome = _plan_one(employee, pending, version, due_date=due_date, zone_name=zone_name, due_at=due_at)
            assignment = pending if outcome != AssignmentOperationResult.Outcome.INACTIVE else None
            if outcome is None:
                try:
                    with transaction.atomic():
                        assignment = Assignment.objects.create(
                            employee=employee, training=version.training, version=version,
                            renews=recipient.renews,
                            cycle_number=recipient.renews.cycle_number + 1 if recipient.renews else 1,
                            due_date=due_date, due_timezone=zone_name, due_at=due_at,
                        )
                        assignment_created(assignment, now=now)
                    outcome = AssignmentOperationResult.Outcome.CREATED
                except IntegrityError:
                    pending = Assignment.objects.outstanding().filter(employee=employee, training=version.training).first()
                    if pending:
                        assignment = pending
                        outcome = _plan_one(employee, pending, version, due_date=due_date,
                                            zone_name=zone_name, due_at=due_at)
                    elif recipient.renews:
                        assignment = Assignment.objects.filter(renews=recipient.renews).with_ending().exclude(
                            ending_value=Ending.CANCELED).first()
                        outcome = AssignmentOperationResult.Outcome.ALREADY_ASSIGNED
                    else:
                        raise
            results.append(AssignmentOperationResult.objects.create(
                operation=operation, employee=employee, outcome=outcome, assignment=assignment,
                detail=_OUTCOME_DETAILS[outcome],
            ))
        return results


def _administrator_can_change(actor):
    if not is_administrator(actor):
        raise AssignmentAccessError("Administrator access is required.")


def _require_outstanding(assignment, message):
    if not assignment.is_outstanding:
        raise AssignmentConflict(message)


def change_assignment_deadline(*, assignment_id, actor, due_date_value, now=None):
    _administrator_can_change(actor)
    now = now or timezone.now()
    new_date = _parse_date(due_date_value)
    with transaction.atomic():
        assignment = Assignment.objects.select_for_update().get(pk=assignment_id, employee__is_demo=actor.is_demo)
        _require_outstanding(assignment, "Only an active, incomplete assignment can change its deadline.")
        if new_date == assignment.due_date:
            return assignment, None
        new_date, zone_name, due_at = validate_new_due_date(new_date, now=now)
        history = AssignmentHistory.objects.create(
            assignment=assignment, actor=actor, kind=AssignmentHistory.Kind.DEADLINE_CHANGED,
            occurred_at=now,
            old_due_date=assignment.due_date, new_due_date=new_date,
            old_due_timezone=assignment.due_timezone, new_due_timezone=zone_name,
            old_due_at=assignment.due_at, new_due_at=due_at,
            old_due_revision=assignment.due_revision, new_due_revision=assignment.due_revision + 1,
        )
        old_revision = assignment.due_revision
        assignment.due_date = new_date
        assignment.due_timezone = zone_name
        assignment.due_at = due_at
        assignment.due_revision += 1
        assignment.save(update_fields=["due_date", "due_timezone", "due_at", "due_revision"])
        deadline_changed(assignment, old_revision=old_revision, now=now)
        return assignment, history


def _clean_reason(reason, *, message):
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise AssignmentInputError(message)
    return reason


@dataclass(frozen=True)
class _EndingRule:
    timestamp: str
    kind: str
    conflict: str
    reason_message: str


_ENDINGS = {
    Ending.CANCELED: _EndingRule(
        "canceled_at", AssignmentHistory.Kind.CANCELED,
        "Only an active, incomplete assignment can be canceled.",
        "Enter a cancellation reason of at most 1000 characters.",
    ),
    Ending.WAIVED: _EndingRule(
        "waived_at", AssignmentHistory.Kind.WAIVED,
        "Only an active, incomplete assignment can be waived.",
        "Enter a waiver reason of at most 1000 characters.",
    ),
}


def _record_ending(assignment, *, ending, actor, reason, now):
    rule = _ENDINGS[ending]
    history = AssignmentHistory.objects.create(
        assignment=assignment, actor=actor, kind=rule.kind,
        occurred_at=now, old_due_date=assignment.due_date, new_due_date=assignment.due_date,
        old_due_timezone=assignment.due_timezone, new_due_timezone=assignment.due_timezone,
        old_due_at=assignment.due_at, new_due_at=assignment.due_at,
        old_due_revision=assignment.due_revision, new_due_revision=assignment.due_revision,
        **{f"new_{rule.timestamp}": now}, reason=reason,
    )
    setattr(assignment, rule.timestamp, now)
    assignment.save(update_fields=[rule.timestamp])
    assignment_ended(assignment, now=now)
    return history


def end_assignment(*, assignment_id, ending, actor, reason, now=None):
    _administrator_can_change(actor)
    rule = _ENDINGS[ending]
    reason = _clean_reason(reason, message=rule.reason_message)
    now = now or timezone.now()
    with transaction.atomic():
        assignment = Assignment.objects.select_for_update().get(pk=assignment_id, employee__is_demo=actor.is_demo)
        if ending == Ending.WAIVED and assignment.employee_id == actor.id:
            raise AssignmentAccessError("Another administrator must waive your own assignment.")
        _require_outstanding(assignment, rule.conflict)
        history = _record_ending(assignment, ending=ending, actor=actor, reason=reason, now=now)
        return assignment, history


def cancel_assignment(*, assignment_id, actor, reason, now=None):
    return end_assignment(assignment_id=assignment_id, ending=Ending.CANCELED,
                          actor=actor, reason=reason, now=now)


def waive_assignment(*, assignment_id, actor, reason, now=None):
    return end_assignment(assignment_id=assignment_id, ending=Ending.WAIVED,
                          actor=actor, reason=reason, now=now)


def archive_assignment(*, assignment_id, actor, reason, now=None):
    _administrator_can_change(actor)
    reason = _clean_reason(reason, message="Enter an archive reason of at most 1000 characters.")
    now = now or timezone.now()
    with transaction.atomic():
        assignment = Assignment.objects.select_for_update().get(pk=assignment_id, employee__is_demo=actor.is_demo)
        if assignment.archived_at:
            return assignment
        if assignment.is_outstanding:
            _record_ending(assignment, ending=Ending.CANCELED, actor=actor, reason=reason, now=now)
        AssignmentHistory.objects.create(
            assignment=assignment, actor=actor, kind=AssignmentHistory.Kind.ARCHIVED,
            occurred_at=now, old_archived_at=None, new_archived_at=now, reason=reason,
        )
        assignment.archived_at = now
        assignment.save(update_fields=["archived_at"])
        return assignment


def restore_assignment(*, assignment_id, actor, now=None):
    _administrator_can_change(actor)
    now = now or timezone.now()
    with transaction.atomic():
        assignment = Assignment.objects.select_for_update().get(pk=assignment_id, employee__is_demo=actor.is_demo)
        if not assignment.archived_at:
            return assignment
        AssignmentHistory.objects.create(
            assignment=assignment, actor=actor, kind=AssignmentHistory.Kind.RESTORED,
            occurred_at=now, old_archived_at=assignment.archived_at, new_archived_at=None,
        )
        assignment.archived_at = None
        assignment.save(update_fields=["archived_at"])
        return assignment


def learner_access(assignment_id, employee, *, lock=False, queryset=None):
    assignments = queryset if queryset is not None else Assignment.objects.select_related("version")
    if lock:
        assignments = assignments.select_for_update()
    try:
        assignment = assignments.get(pk=assignment_id)
    except (Assignment.DoesNotExist, ValidationError, ValueError):
        return LearnerAccess(None, LearnerDenial.MISSING)
    if assignment.employee_id != employee.id:
        return LearnerAccess(assignment, LearnerDenial.NOT_YOURS)
    if not employee.is_active:
        return LearnerAccess(assignment, LearnerDenial.INACTIVE)
    if assignment.ending == Ending.CANCELED:
        return LearnerAccess(assignment, LearnerDenial.CANCELED)
    if assignment.ending == Ending.WAIVED:
        return LearnerAccess(assignment, LearnerDenial.WAIVED)
    return LearnerAccess(assignment, None)


def _owned_assignment(assignment_id, employee):
    access = learner_access(assignment_id, employee, lock=True)
    if access.denial == LearnerDenial.MISSING:
        raise Assignment.DoesNotExist
    if not access.can_act:
        raise AssignmentAccessError(access.message)
    return access.assignment


def visit_slide(*, assignment_id, slide_id, employee, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        assignment = _owned_assignment(assignment_id, employee)
        slide = Slide.objects.get(pk=slide_id)
        if slide.version_id != assignment.version_id:
            raise AssignmentAccessError("This slide is not in the assigned version.")
        if not assignment.completed_at:
            if not assignment.started_at:
                assignment.started_at = now
            assignment.resume_slide = slide
            assignment.save(update_fields=["started_at", "resume_slide"])
        return assignment, slide


def _complete_if_satisfied(assignment, *, now, passed_quiz=False):
    if assignment.completed_at:
        return False
    if passed_quiz or (
        slides_acknowledged(assignment) and not Quiz.objects.filter(version=assignment.version).exists()
    ):
        assignment.completed_at = now
        return True
    return False


def acknowledge_slide(*, assignment_id, slide_id, employee, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        assignment = _owned_assignment(assignment_id, employee)
        slide = Slide.objects.get(pk=slide_id)
        if slide.version_id != assignment.version_id:
            raise AssignmentAccessError("This slide is not in the assigned version.")
        if assignment.completed_at:
            return assignment
        AssignmentSlideProgress.objects.get_or_create(
            assignment=assignment, slide=slide, defaults={"acknowledged_at": now},
        )
        if not assignment.started_at:
            assignment.started_at = now
        assignment.resume_slide = slide
        completed = _complete_if_satisfied(assignment, now=now)
        assignment.save(update_fields=["started_at", "resume_slide", "completed_at"])
        if completed:
            assignment_ended(assignment, now=now)
        return assignment


@transaction.atomic
def visit_document(*, assignment_id, document_id, employee, now=None, acknowledge=False):
    from .models import AssignmentDocumentProgress, OutlineItem

    now = now or timezone.now()
    assignment = _owned_assignment(assignment_id, employee)
    document = OutlineItem.objects.get(pk=document_id, kind="document")
    if assignment.version.content_format != "documents" or document.version_id != assignment.version_id:
        raise AssignmentAccessError("This document is not in the assigned version.")
    if not assignment.completed_at:
        if not assignment.started_at:
            assignment.started_at = now
        assignment.resume_document = document
        if acknowledge:
            AssignmentDocumentProgress.objects.get_or_create(assignment=assignment, document=document,
                defaults={"acknowledged_at": now})
        completed = _complete_if_satisfied(assignment, now=now)
        assignment.save(update_fields=["started_at", "resume_document", "completed_at"])
        if completed:
            assignment_ended(assignment, now=now)
    return assignment, document
