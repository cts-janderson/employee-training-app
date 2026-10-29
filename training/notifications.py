import uuid
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as utc_timezone
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .employees import is_administrator
from .mail import AmbiguousMailError, GraphMailSender, PermanentMailError, TransientMailError
from .models import (
    Assignment, EmployeeActivityChange, Notification, NotificationDeliveryAttempt,
    NotificationReconciliation,
)


class NotificationError(Exception):
    pass


class ManualNudgeIneligible(NotificationError):
    pass


class ManualNudgeRateLimited(NotificationError):
    def __init__(self, next_eligible_at):
        self.next_eligible_at = next_eligible_at
        super().__init__("A manual reminder was already requested in the last 24 hours.")


class TransientDeliveryError(NotificationError):
    def __init__(self, code="temporary_failure", retry_after=None):
        self.code = code
        self.retry_after = retry_after


class PermanentDeliveryError(NotificationError):
    def __init__(self, code="permanent_failure"):
        self.code = code


class AmbiguousDeliveryError(NotificationError):
    def __init__(self, code="delivery_outcome_unknown"):
        self.code = code


class LocalEmailSink:
    def send(self, *, delivery_key, recipient, subject, body):
        if (not settings.DEBUG or not settings.DEV_AUTH_STUB
                or settings.EMAIL_BACKEND != "django.core.mail.backends.locmem.EmailBackend"
                or not recipient.endswith(".test")):
            raise PermanentDeliveryError("local_sink_unavailable")
        EmailMessage(subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL,
                     to=[recipient], headers={"X-Training-Delivery-Key": str(delivery_key)}).send()
        return f"local-sink:{delivery_key}"


class GraphNotificationSender:
    """Translate Graph outcomes into the delivery errors the retry and reconciliation flow expects."""

    def __init__(self, mail_sender=None):
        self.mail_sender = mail_sender or GraphMailSender()

    def send(self, *, delivery_key, recipient, subject, body):
        if recipient.endswith(".test"):
            raise PermanentDeliveryError("reserved_test_address")
        try:
            return self.mail_sender.send(recipient=recipient, subject=subject, body=body, delivery_key=delivery_key)
        except TransientMailError as exc:
            raise TransientDeliveryError(exc.code, exc.retry_after) from exc
        except PermanentMailError as exc:
            raise PermanentDeliveryError(exc.code) from exc
        except AmbiguousMailError as exc:
            raise AmbiguousDeliveryError(exc.code) from exc


def notification_sender():
    """The sender for the configured email backend: Graph in production, the local sink otherwise."""
    return GraphNotificationSender() if settings.EMAIL_BACKEND == settings.GRAPH_EMAIL_BACKEND else LocalEmailSink()


def _safe_error_code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value) else "delivery_error"


REMINDER_KINDS = {Notification.Kind.THREE_DAY_REMINDER, Notification.Kind.DUE_DAY_REMINDER}
MANUAL_NUDGE_INTERVAL = timedelta(hours=24)


def _manual_next_at(latest_requested_at, now):
    if latest_requested_at is None:
        return None
    next_at = latest_requested_at + MANUAL_NUDGE_INTERVAL
    return next_at if next_at > now else None


def next_manual_nudge_time(assignment, *, now=None):
    now = now or timezone.now()
    latest = Notification.objects.filter(
        assignment=assignment, kind=Notification.Kind.MANUAL_NUDGE,
    ).order_by("-requested_at").values_list("requested_at", flat=True).first()
    return _manual_next_at(latest, now)


def manual_nudge_problem(assignment):
    """Return why the assignment cannot receive a manual reminder, ignoring the rolling limit, or ""."""
    if not assignment.is_outstanding:
        return "Only incomplete, pending assignments can receive a manual reminder."
    if not assignment.employee.is_active:
        return "Inactive employees cannot receive a manual reminder."
    return ""


def is_upcoming_reminder(notification, assignment, now):
    """Cheap display check for a current-revision reminder still waiting for its scheduled time."""
    return (notification.kind in REMINDER_KINDS
            and notification.due_revision == assignment.due_revision
            and notification.status in {Notification.Status.PENDING, Notification.Status.RETRY}
            and notification.scheduled_at is not None and notification.scheduled_at > now
            and not manual_nudge_problem(assignment))


@dataclass(frozen=True)
class FollowUpSummary:
    manual_next_at: datetime | None
    manual_can_send: bool
    upcoming_reminders: list
    notifications: list

    @property
    def next_scheduled_reminder(self):
        return self.upcoming_reminders[0] if self.upcoming_reminders else None


def follow_up_summaries(assignments, *, now):
    assignments_by_id = {assignment.id: assignment for assignment in assignments}
    notifications = {assignment_id: [] for assignment_id in assignments_by_id}
    upcoming = {assignment_id: [] for assignment_id in assignments_by_id}
    latest_nudge = {}
    if assignments_by_id:
        items = Notification.objects.filter(assignment_id__in=assignments_by_id).order_by(
            "scheduled_at", "created_at",
        )
        for item in items:
            assignment_id = item.assignment_id
            notifications[assignment_id].append(item)
            if item.kind == Notification.Kind.MANUAL_NUDGE:
                previous = latest_nudge.get(assignment_id)
                if previous is None or item.requested_at > previous:
                    latest_nudge[assignment_id] = item.requested_at
            elif is_upcoming_reminder(item, assignments_by_id[assignment_id], now):
                upcoming[assignment_id].append(item)
    summaries = {}
    for assignment_id, assignment in assignments_by_id.items():
        manual_next_at = _manual_next_at(latest_nudge.get(assignment_id), now)
        summaries[assignment_id] = FollowUpSummary(
            manual_next_at=manual_next_at,
            manual_can_send=not manual_nudge_problem(assignment) and manual_next_at is None,
            upcoming_reminders=upcoming[assignment_id], notifications=notifications[assignment_id],
        )
    return summaries


def request_manual_nudge(*, assignment_id, actor, now=None):
    _administrator(actor)
    now = now or timezone.now()
    with transaction.atomic():
        assignment = Assignment.objects.select_for_update().select_related("employee").get(
            pk=assignment_id, employee__is_demo=actor.is_demo,
        )
        problem = manual_nudge_problem(assignment)
        if problem:
            raise ManualNudgeIneligible(problem)
        next_eligible_at = next_manual_nudge_time(assignment, now=now)
        if next_eligible_at is not None:
            raise ManualNudgeRateLimited(next_eligible_at)
        return Notification.objects.create(
            assignment=assignment, kind=Notification.Kind.MANUAL_NUDGE,
            due_revision=assignment.due_revision, requested_by=actor, requested_at=now,
            next_attempt_at=now, status_changed_at=now,
        )


def reminder_times(due_date, zone_name):
    zone = ZoneInfo(zone_name)
    return {
        Notification.Kind.THREE_DAY_REMINDER: datetime.combine(
            due_date - timedelta(days=3), time(9), tzinfo=zone,
        ).astimezone(utc_timezone.utc),
        Notification.Kind.DUE_DAY_REMINDER: datetime.combine(
            due_date, time(9), tzinfo=zone,
        ).astimezone(utc_timezone.utc),
    }


def schedule_reminders(assignment, *, now=None):
    """Create one durable record per reminder and deadline revision inside the caller's transaction."""
    now = now or timezone.now()
    records = []
    for kind, scheduled_at in reminder_times(assignment.due_date, assignment.due_timezone).items():
        elapsed = scheduled_at < now
        notification, _ = Notification.objects.get_or_create(
            assignment=assignment, kind=kind, due_revision=assignment.due_revision,
            defaults={
                "scheduled_at": scheduled_at, "next_attempt_at": scheduled_at,
                "status": Notification.Status.SUPPRESSED if elapsed else Notification.Status.PENDING,
                "suppression_reason": "schedule_elapsed" if elapsed else "",
                "status_changed_at": now,
            },
        )
        records.append(notification)
    return records


def assignment_created(assignment, *, now):
    notification = Notification.objects.create(
        assignment=assignment, kind=Notification.Kind.ASSIGNED,
        due_revision=assignment.due_revision, next_attempt_at=now, status_changed_at=now,
    )
    schedule_reminders(assignment, now=now)
    return notification


def deadline_changed(assignment, *, old_revision, now):
    notification = Notification.objects.create(
        assignment=assignment, kind=Notification.Kind.DEADLINE_CHANGED,
        due_revision=assignment.due_revision, next_attempt_at=now, status_changed_at=now,
    )
    invalidate_prior_reminders(assignment, old_revision=old_revision, now=now)
    schedule_reminders(assignment, now=now)
    return notification


def assignment_ended(assignment, *, now):
    reason = f"assignment_{assignment.ending}"
    return Notification.objects.filter(
        assignment=assignment, kind__in=[*REMINDER_KINDS, Notification.Kind.MANUAL_NUDGE],
        status__in=[Notification.Status.PENDING, Notification.Status.RETRY,
                    Notification.Status.BLOCKED, Notification.Status.EXHAUSTED],
    ).update(status=Notification.Status.SUPPRESSED, suppression_reason=reason, status_changed_at=now)


def invalidate_prior_reminders(assignment, *, old_revision, now=None):
    now = now or timezone.now()
    return Notification.objects.filter(
        assignment=assignment, kind__in=REMINDER_KINDS, due_revision=old_revision,
        status__in=[Notification.Status.PENDING, Notification.Status.RETRY,
                    Notification.Status.BLOCKED, Notification.Status.EXHAUSTED],
    ).update(status=Notification.Status.SUPPRESSED, suppression_reason="deadline_superseded",
             status_changed_at=now)


def _inactive_during_reminder_window(employee, scheduled_at, now):
    activity = EmployeeActivityChange.objects.filter(employee=employee, changed_at__lte=now)
    state_at_schedule = activity.filter(changed_at__lte=scheduled_at).order_by("-changed_at", "-id").first()
    if state_at_schedule is not None and not state_at_schedule.is_active:
        return True
    if state_at_schedule is None:
        first_later_change = activity.filter(changed_at__gt=scheduled_at).order_by("changed_at", "id").first()
        if first_later_change is not None and not first_later_change.was_active:
            return True
    return activity.filter(changed_at__gt=scheduled_at, is_active=False).exists()


def _suppression_reason(notification, assignment, *, now=None):
    now = now or timezone.now()
    if assignment.ending:
        return f"assignment_{assignment.ending}"
    if notification.kind in {Notification.Kind.DEADLINE_CHANGED, *REMINDER_KINDS} and notification.due_revision != assignment.due_revision:
        return "deadline_superseded"
    if notification.kind in REMINDER_KINDS:
        if notification.scheduled_at is None:
            return "invalid_schedule"
        if now >= assignment.due_at:
            return "reminder_expired"
        if not assignment.employee.is_active:
            return "employee_inactive"
        if _inactive_during_reminder_window(assignment.employee, notification.scheduled_at, now):
            return "employee_inactive_during_window"
        due_day = reminder_times(assignment.due_date, assignment.due_timezone)[Notification.Kind.DUE_DAY_REMINDER]
        if notification.kind == Notification.Kind.THREE_DAY_REMINDER and now >= due_day:
            return "reminder_superseded"
    elif not assignment.employee.is_active:
        return "employee_inactive"
    return ""


def _verified_recipient(employee):
    email = employee.email.strip().lower()
    verified = employee.verified_email.strip().lower()
    if not email or email != verified:
        return None
    # Demo employees may only receive mail at reserved .test addresses.
    if employee.is_demo and not email.endswith(".test"):
        return None
    return email


def _message(notification, assignment):
    title = assignment.version.title
    kind_label = {
        Notification.Kind.ASSIGNED: "assigned",
        Notification.Kind.DEADLINE_CHANGED: "deadline changed",
        Notification.Kind.THREE_DAY_REMINDER: "reminder: due in three days",
        Notification.Kind.DUE_DAY_REMINDER: "reminder: due today",
        Notification.Kind.MANUAL_NUDGE: "manual reminder",
    }[notification.kind]
    subject = f"Training {kind_label}: {title}"
    link = f"{settings.APP_BASE_URL}{reverse('assignment_detail', args=[assignment.id])}"
    body = (f"Training: {title}\n"
            f"Due date: {assignment.due_date.isoformat()} ({assignment.due_timezone})\n"
            f"Open your assignment: {link}\n")
    return subject, body


def recover_stale_claims(*, now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.NOTIFICATION_CLAIM_LEASE_SECONDS)
    return Notification.objects.filter(
        status=Notification.Status.SENDING, claimed_at__lt=cutoff,
    ).update(status=Notification.Status.AMBIGUOUS, last_error_code="claim_expired",
             claim_token=None, claimed_at=None, status_changed_at=now)


def claim_next_notification(*, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        notification = Notification.objects.select_for_update(skip_locked=True).filter(
            status__in=[Notification.Status.PENDING, Notification.Status.RETRY],
            next_attempt_at__lte=now,
        ).order_by("next_attempt_at", "created_at").first()
        if notification is None:
            return None
        token = uuid.uuid4()
        notification.status = Notification.Status.SENDING
        notification.status_changed_at = now
        notification.claim_token = token
        notification.claimed_at = now
        notification.save(update_fields=["status", "status_changed_at", "claim_token", "claimed_at"])
        return notification.id, token


_RELEASED_CLAIM = {"claim_token": None, "claimed_at": None}


def _save_transition(notification, now, changes):
    """Apply field changes with a new status time, saving exactly the changed fields."""
    changes = {**changes, "status_changed_at": now}
    for field, value in changes.items():
        setattr(notification, field, value)
    notification.save(update_fields=list(changes))
    return notification


def next_state(notification, assignment, *, now):
    """Decide whether an undelivered notification may be (re)delivered.

    Returns field changes whose ``status`` is SUPPRESSED when delivery is no longer wanted, BLOCKED when the
    employee has no current verified address, or PENDING (requeued at its schedule with a fresh retry budget)
    when delivery may proceed. Suppression takes precedence over a blocked address. Reads the employee's
    activity history; the caller owns locking and saving.
    """
    reason = _suppression_reason(notification, assignment, now=now)
    if reason:
        return {"status": Notification.Status.SUPPRESSED, "suppression_reason": reason}
    if _verified_recipient(assignment.employee) is None:
        return {"status": Notification.Status.BLOCKED, "last_error_code": "unverified_address"}
    scheduled_at = notification.scheduled_at
    return {
        "status": Notification.Status.PENDING,
        "next_attempt_at": max(now, scheduled_at) if scheduled_at else now,
        "automatic_failures": 0, "last_error_code": "",
    }


def delivery_result(notification, error, *, now):
    """Map a sender outcome (an exception, or None when accepted) to an attempt outcome, error code, and changes."""
    Outcome = NotificationDeliveryAttempt.Outcome
    if error is None:
        return Outcome.ACCEPTED, "", {"status": Notification.Status.SENT, "sent_at": now, "last_error_code": ""}
    if isinstance(error, TransientDeliveryError):
        code = _safe_error_code(error.code)
        failures = notification.automatic_failures + 1
        changes = {"automatic_failures": failures, "last_error_code": code}
        if failures >= settings.NOTIFICATION_MAX_AUTO_ATTEMPTS:
            changes["status"] = Notification.Status.EXHAUSTED
        else:
            changes["status"] = Notification.Status.RETRY
            # Back off exponentially, waiting longer when the provider asks for it.
            delay = max(60 * 2 ** (failures - 1), getattr(error, "retry_after", None) or 0)
            changes["next_attempt_at"] = now + timedelta(seconds=min(3600, delay))
        return Outcome.TRANSIENT_FAILURE, code, changes
    if isinstance(error, PermanentDeliveryError):
        code = _safe_error_code(error.code)
        return Outcome.PERMANENT_FAILURE, code, {"status": Notification.Status.EXHAUSTED, "last_error_code": code}
    code = _safe_error_code(error.code) if isinstance(error, AmbiguousDeliveryError) else "unexpected_sender_error"
    return Outcome.AMBIGUOUS, code, {"status": Notification.Status.AMBIGUOUS, "last_error_code": code}


# Lock an assignment before any of its notifications.
def dispatch_claimed(notification_id, claim_token, *, now=None, sender=None):
    now = now or timezone.now()
    sender = sender or notification_sender()
    with transaction.atomic():
        assignment_id = Notification.objects.values_list("assignment_id", flat=True).get(pk=notification_id)
        assignment = Assignment.objects.select_for_update().select_related("employee", "version").get(
            pk=assignment_id,
        )
        notification = Notification.objects.select_for_update().get(pk=notification_id)
        if notification.status != Notification.Status.SENDING or notification.claim_token != claim_token:
            raise NotificationError("The notification claim is no longer active.")
        decision = next_state(notification, assignment, now=now)
        if decision["status"] != Notification.Status.PENDING:
            return _save_transition(notification, now, {**decision, **_RELEASED_CLAIM})
        # Deliverable: the requeue changes do not apply to a claimed send, which keeps its retry budget.
        recipient = _verified_recipient(assignment.employee)
        subject, body = _message(notification, assignment)
        number = notification.attempt_count + 1
        attempt = NotificationDeliveryAttempt.objects.create(
            notification=notification, number=number, started_at=now,
            recipient_email=recipient, subject=subject, body=body,
            due_date=assignment.due_date, due_timezone=assignment.due_timezone,
            due_at=assignment.due_at, due_revision=assignment.due_revision,
            delivery_key=notification.id,
        )
        try:
            reference = sender.send(
                delivery_key=notification.id, recipient=recipient, subject=subject, body=body,
            )
        except Exception as exc:
            attempt.outcome, attempt.error_code, changes = delivery_result(notification, exc, now=now)
        else:
            attempt.outcome, attempt.error_code, changes = delivery_result(notification, None, now=now)
            attempt.provider_reference = str(reference)[:200]
            changes["provider_reference"] = attempt.provider_reference
        attempt.finished_at = now
        attempt.save(update_fields=["finished_at", "outcome", "provider_reference", "error_code"])
        return _save_transition(notification, now, {"attempt_count": number, **changes, **_RELEASED_CLAIM})


def process_ready_notifications(*, limit=100, now=None, sender=None, should_stop=None):
    """Dispatch up to ``limit`` due notifications, checking ``should_stop`` before each new claim."""
    now = now or timezone.now()
    sender = sender or notification_sender()
    recover_stale_claims(now=now)
    processed = []
    for _ in range(limit):
        if should_stop and should_stop():
            break
        claim = claim_next_notification(now=now)
        if claim is None:
            break
        processed.append(dispatch_claimed(*claim, now=now, sender=sender))
    return processed


def _administrator(actor):
    if not is_administrator(actor):
        raise NotificationError("Administrator access is required.")


def retry_notification(*, notification_id, actor, now=None):
    _administrator(actor)
    now = now or timezone.now()
    with transaction.atomic():
        assignment_id = Notification.objects.values_list("assignment_id", flat=True).get(
            pk=notification_id, assignment__employee__is_demo=actor.is_demo)
        assignment = Assignment.objects.select_for_update().select_related("employee").get(pk=assignment_id)
        notification = Notification.objects.select_for_update().get(pk=notification_id)
        if notification.status not in {Notification.Status.BLOCKED, Notification.Status.EXHAUSTED}:
            raise NotificationError("Only blocked or exhausted notifications can be retried.")
        decision = next_state(notification, assignment, now=now)
        if decision["status"] == Notification.Status.BLOCKED:
            raise NotificationError("The employee does not have a current verified address.")
        return _save_transition(notification, now, decision)


def reconcile_notification(*, notification_id, actor, decision, reason, now=None):
    _administrator(actor)
    reason = reason.strip()
    if decision not in NotificationReconciliation.Decision.values or not reason or len(reason) > 1000:
        raise NotificationError("Choose a reconciliation outcome and enter a reason of at most 1000 characters.")
    now = now or timezone.now()
    with transaction.atomic():
        assignment_id = Notification.objects.values_list("assignment_id", flat=True).get(
            pk=notification_id, assignment__employee__is_demo=actor.is_demo)
        assignment = Assignment.objects.select_for_update().select_related("employee").get(pk=assignment_id)
        notification = Notification.objects.select_for_update().get(pk=notification_id)
        if notification.status != Notification.Status.AMBIGUOUS:
            raise NotificationError("Only ambiguous notifications can be reconciled.")
        NotificationReconciliation.objects.create(
            notification=notification, actor=actor, decision=decision, reason=reason, decided_at=now,
        )
        if decision == NotificationReconciliation.Decision.DELIVERED:
            changes = {"status": Notification.Status.SENT, "sent_at": now}
        else:
            changes = next_state(notification, assignment, now=now)
        return _save_transition(notification, now, changes)
