import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone as utc_timezone
from threading import Barrier, Event
from unittest import skipUnless
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignments import (
    acknowledge_slide, archive_assignment, cancel_assignment, change_assignment_deadline, create_assignments,
)
from .employees import update_demo_employee
from .models import (
    Employee, Notification, NotificationDeliveryAttempt, NotificationReconciliation,
    Slide, Training, TrainingVersion,
)
from .notifications import (
    AmbiguousDeliveryError, PermanentDeliveryError, TransientDeliveryError, claim_next_notification,
    NotificationError, delivery_result, dispatch_claimed, next_state, process_ready_notifications, reconcile_notification,
    recover_stale_claims, retry_notification, follow_up_summaries, next_manual_nudge_time,
)


class FakeSender:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def send(self, **message):
        self.calls.append(message)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class NotificationFixtures:
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(
            name="Demo Administrator", email="demo.admin@example.test",
            verified_email="demo.admin@example.test",
            role=Employee.Role.ADMINISTRATOR, is_demo=True,
        )
        self.alice = Employee.objects.create(name="Alice", email="alice@example.test",
                                             verified_email="alice@example.test", is_demo=True)
        self.bob = Employee.objects.create(name="Bob", email="bob@example.test",
                                           verified_email="bob@example.test", is_demo=True)
        self.carol = Employee.objects.create(name="Carol", email="carol@example.test",
                                             verified_email="carol@example.test", is_demo=True)
        self.training = Training.objects.create()
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Data Safety")
        self.slide = Slide.objects.create(version=self.version, position=1, header="Read", body="Material")
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.published_at = timezone.now()
        self.version.save()
        self.now = timezone.now()
        self.today = self.now.astimezone(ZoneInfo("America/New_York")).date()

    def assign(self, employees=None, *, operation_id=None, due_date=None, now=None):
        return create_assignments(
            operation_id=operation_id or uuid.uuid4(), actor=self.admin, version_id=self.version.id,
            employee_ids=[employee.id for employee in (employees or [self.alice])],
            due_date_value=due_date or (self.today + timedelta(days=2)).isoformat(), now=now or self.now,
        )


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test",
                   ORG_TIME_ZONE="America/New_York", APP_BASE_URL="http://127.0.0.1:8000")
class NotificationWorkflowTests(NotificationFixtures, TestCase):
    def test_bulk_outbox_replay_local_sink_and_ownership_link(self):
        operation_id = uuid.uuid4()
        results = self.assign([self.alice, self.bob], operation_id=operation_id)
        assigned = Notification.objects.filter(kind=Notification.Kind.ASSIGNED)
        self.assertEqual(assigned.count(), 2)
        self.assertEqual({item.due_revision for item in assigned}, {0})
        replay = self.assign([self.alice, self.bob], operation_id=operation_id)
        self.assertEqual({row.id for row in results}, {row.id for row in replay})
        self.assertEqual(assigned.count(), 2)
        self.assign([self.alice])
        self.assertEqual(assigned.count(), 2)
        delivered = process_ready_notifications(now=self.now)
        self.assertEqual(len(delivered), 2)
        self.assertEqual(len(mail.outbox), 2)
        notification = Notification.objects.get(assignment__employee=self.alice, kind=Notification.Kind.ASSIGNED)
        self.assertEqual(notification.status, Notification.Status.SENT)
        attempt = notification.delivery_attempts.get()
        self.assertEqual(attempt.recipient_email, self.alice.email)
        self.assertIn("Data Safety", attempt.subject)
        self.assertIn("Due date:", attempt.body)
        self.assertNotIn("correct answer", attempt.body)
        self.assertEqual(attempt.delivery_key, notification.id)
        self.assertEqual(attempt.due_revision, 0)
        with self.assertRaises(ValidationError):
            attempt.body = "changed"
            attempt.save()
        attempt.refresh_from_db()
        self.assertEqual(process_ready_notifications(now=self.now), [])
        self.assertEqual(len(mail.outbox), 2)
        path = urlsplit(attempt.body.split("Open your assignment: ", 1)[1].strip()).path
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.bob.id)})
        self.assertEqual(self.client.get(path).status_code, 404)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        self.assertEqual(self.client.get(path).status_code, 200)

    def test_transient_failures_retry_with_history_then_manual_retry_after_exhaustion(self):
        assignment = self.assign()[0].assignment
        sender = FakeSender([TransientDeliveryError("temporary_failure"),
                             TransientDeliveryError("temporary_failure"),
                             TransientDeliveryError("temporary_failure"), "accepted-after-retry"])
        notification = assignment.notifications.get(kind=Notification.Kind.ASSIGNED)
        self.assertEqual(len(process_ready_notifications(now=self.now, sender=sender)), 1)
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.RETRY)
        self.assertEqual(notification.next_attempt_at, self.now + timedelta(seconds=60))
        self.assertEqual(process_ready_notifications(now=self.now + timedelta(seconds=59), sender=sender), [])
        process_ready_notifications(now=self.now + timedelta(seconds=60), sender=sender)
        notification.refresh_from_db()
        self.assertEqual(notification.next_attempt_at, self.now + timedelta(seconds=180))
        process_ready_notifications(now=self.now + timedelta(seconds=180), sender=sender)
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.EXHAUSTED)
        self.assertEqual(notification.delivery_attempts.count(), 3)
        self.assertEqual({attempt.delivery_key for attempt in notification.delivery_attempts.all()}, {notification.id})
        self.assertEqual(self.client.post(reverse("retry_notification", args=[notification.id])).status_code, 302)
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.PENDING)
        process_ready_notifications(now=timezone.now() + timedelta(seconds=1), sender=sender)
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.delivery_attempts.count(), 4)
        self.assertEqual(notification.provider_reference, "accepted-after-retry")
        self.assertEqual(Notification.objects.filter(kind=Notification.Kind.ASSIGNED).count(), 1)

    def test_completed_canceled_and_inactive_work_is_suppressed_before_sending(self):
        results = self.assign([self.alice, self.bob, self.carol])
        by_employee = {row.employee_id: row.assignment for row in results}
        acknowledge_slide(assignment_id=by_employee[self.alice.id].id,
                          slide_id=self.slide.id, employee=self.alice, now=self.now)
        cancel_assignment(assignment_id=by_employee[self.bob.id].id,
                          actor=self.admin, reason="Withdrawn", now=self.now)
        self.carol.is_active = False
        self.carol.save(update_fields=["is_active"])
        delivered = process_ready_notifications(now=self.now)
        self.assertEqual({item.status for item in delivered}, {Notification.Status.SUPPRESSED})
        self.assertEqual({item.suppression_reason for item in delivered},
                         {"assignment_completed", "assignment_canceled", "employee_inactive"})
        self.assertEqual(NotificationDeliveryAttempt.objects.count(), 0)
        self.assertFalse(hasattr(mail, "outbox") and mail.outbox)

    def test_deadline_intents_are_immediate_unique_and_obsolete_revisions_suppress(self):
        after_nine = datetime.combine(self.today, time(10, 15), tzinfo=ZoneInfo("America/New_York")).astimezone(utc_timezone.utc)
        assignment = self.assign(now=after_nine)[0].assignment
        first_due = self.today + timedelta(days=3)
        second_due = self.today + timedelta(days=4)
        _, first_history = change_assignment_deadline(
            assignment_id=assignment.id, actor=self.admin, due_date_value=first_due, now=after_nine,
        )
        self.assertIsNotNone(first_history)
        _, no_history = change_assignment_deadline(
            assignment_id=assignment.id, actor=self.admin, due_date_value=first_due, now=after_nine,
        )
        self.assertIsNone(no_history)
        _, second_history = change_assignment_deadline(
            assignment_id=assignment.id, actor=self.admin, due_date_value=second_due, now=after_nine,
        )
        self.assertIsNotNone(second_history)
        self.assertEqual(Notification.objects.filter(assignment=assignment, kind__in=[
            Notification.Kind.ASSIGNED, Notification.Kind.DEADLINE_CHANGED,
        ]).count(), 3)
        self.assertEqual(Notification.objects.get(assignment=assignment,
                                                  kind=Notification.Kind.DEADLINE_CHANGED,
                                                  due_revision=1).next_attempt_at, after_nine)
        delivered = process_ready_notifications(now=after_nine)
        self.assertEqual(len(delivered), 3)
        obsolete = Notification.objects.get(assignment=assignment, kind=Notification.Kind.DEADLINE_CHANGED,
                                            due_revision=1)
        self.assertEqual((obsolete.status, obsolete.suppression_reason),
                         (Notification.Status.SUPPRESSED, "deadline_superseded"))
        latest = Notification.objects.get(assignment=assignment, kind=Notification.Kind.DEADLINE_CHANGED,
                                          due_revision=2)
        self.assertEqual(latest.status, Notification.Status.SENT)
        attempt = latest.delivery_attempts.get()
        self.assertEqual(attempt.due_date, second_due)
        self.assertIn(second_due.isoformat(), attempt.body)
        self.assertEqual(attempt.due_revision, 2)
        self.assertEqual(NotificationDeliveryAttempt.objects.count(), 2)

    def test_current_verified_address_is_used_and_unverified_replacement_blocks(self):
        assignment = self.assign()[0].assignment
        notification = assignment.notifications.get(kind=Notification.Kind.ASSIGNED)
        Employee.objects.filter(pk=self.alice.id).update(email="new.alice@example.test")
        process_ready_notifications(now=self.now)
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.BLOCKED)
        self.assertEqual(notification.last_error_code, "unverified_address")
        self.assertEqual(notification.delivery_attempts.count(), 0)
        self.assertEqual(self.client.post(reverse("retry_notification", args=[notification.id])).status_code, 400)
        update_demo_employee(employee_id=self.alice.id, name="Alice", email="new.alice@example.test",
                             active=True, actor=self.admin)
        new_due = self.today + timedelta(days=4)
        change_assignment_deadline(assignment_id=assignment.id, actor=self.admin,
                                   due_date_value=new_due, now=self.now)
        revised = TrainingVersion.objects.create(training=self.training, version_number=2, title="Revised Safety")
        Slide.objects.create(version=revised, position=1, header="Different", body="Revised content")
        revised.status = TrainingVersion.Status.PUBLISHED
        revised.published_at = self.now
        revised.save()
        self.assertEqual(self.client.post(reverse("retry_notification", args=[notification.id])).status_code, 302)
        process_ready_notifications(now=timezone.now() + timedelta(seconds=1))
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        attempt = notification.delivery_attempts.get()
        self.assertEqual(attempt.recipient_email, "new.alice@example.test")
        self.assertEqual(attempt.due_date, new_due)
        self.assertIn("Data Safety", attempt.body)
        self.assertNotIn("Revised Safety", attempt.body)
        self.assertNotIn("alice@example.test\n", attempt.body)
        self.assertEqual(mail.outbox[0].to, ["new.alice@example.test"])

    def test_retry_after_cancellation_is_suppressed(self):
        assignment = self.assign()[0].assignment
        sender = FakeSender([TransientDeliveryError("temporary_failure")])
        process_ready_notifications(now=self.now, sender=sender)
        notification = assignment.notifications.get(kind=Notification.Kind.ASSIGNED)
        self.assertEqual(notification.status, Notification.Status.RETRY)
        cancel_assignment(assignment_id=assignment.id, actor=self.admin, reason="Withdrawn", now=self.now)
        process_ready_notifications(now=self.now + timedelta(seconds=60), sender=sender)
        notification.refresh_from_db()
        self.assertEqual((notification.status, notification.suppression_reason),
                         (Notification.Status.SUPPRESSED, "assignment_canceled"))
        self.assertEqual(notification.delivery_attempts.count(), 1)

    def test_ambiguous_outcome_requires_reconciliation_before_retry(self):
        assignment = self.assign()[0].assignment
        notification = assignment.notifications.get(kind=Notification.Kind.ASSIGNED)
        sender = FakeSender([AmbiguousDeliveryError("delivery_outcome_unknown")])
        process_ready_notifications(now=self.now, sender=sender)
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.AMBIGUOUS)
        self.assertEqual(process_ready_notifications(now=self.now + timedelta(hours=1)), [])
        self.assertEqual(self.client.post(reverse("retry_notification", args=[notification.id])).status_code, 400)
        self.assertEqual(self.client.post(reverse("reconcile_notification", args=[notification.id]), {
            "decision": "not_delivered", "reason": "Checked local sink log; no accepted message",
        }).status_code, 302)
        self.assertEqual(NotificationReconciliation.objects.filter(notification=notification).count(), 1)
        process_ready_notifications(now=timezone.now() + timedelta(seconds=1))
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.delivery_attempts.count(), 2)
        self.assertEqual(self.client.get(reverse("notification_detail", args=[notification.id])).status_code, 200)

    def test_stale_claim_becomes_ambiguous_and_confirmed_delivery_does_not_resend(self):
        assignment = self.assign()[0].assignment
        notification = assignment.notifications.get(kind=Notification.Kind.ASSIGNED)
        claim = claim_next_notification(now=self.now)
        self.assertEqual(claim[0], notification.id)
        count = recover_stale_claims(now=self.now + timedelta(seconds=301))
        self.assertEqual(count, 1)
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.AMBIGUOUS)
        reconcile_notification(notification_id=notification.id, actor=self.admin,
                               decision=NotificationReconciliation.Decision.DELIVERED,
                               reason="Confirmed in local sink", now=self.now + timedelta(seconds=302))
        self.assertEqual(process_ready_notifications(now=self.now + timedelta(seconds=400)), [])
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.delivery_attempts.count(), 0)
        self.assertContains(self.client.get(reverse("notification_detail", args=[notification.id])),
                            "Confirmed delivered")

    def test_worker_command_and_admin_log_are_local_only(self):
        self.assign()
        call_command("dispatch_notifications", limit=10)
        self.assertEqual(Notification.objects.get(kind=Notification.Kind.ASSIGNED).status, Notification.Status.SENT)
        self.assertContains(self.client.get(reverse("notification_log")), "Data Safety")
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        self.assertEqual(self.client.get(reverse("notification_log")).status_code, 403)
        with override_settings(DEBUG=False):
            from django.core.management.base import CommandError
            with self.assertRaises(CommandError):
                call_command("dispatch_notifications")

    def test_notification_log_filters_and_overview_counts(self):
        self.assign([self.alice, self.bob])
        assigned = Notification.objects.get(kind=Notification.Kind.ASSIGNED, assignment__employee=self.alice)
        assigned.status = Notification.Status.SENT
        assigned.save(update_fields=["status"])

        response = self.client.get(reverse("notification_log"), {
            "status": "sent", "kind": "assigned", "q": "Alice",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["item"].id for row in response.context["notification_rows"]], [assigned.id])
        self.assertEqual(response.context["counts"]["sent"], 1)
        self.assertEqual(response.context["counts"]["total"], Notification.objects.count())
        self.assertContains(response, "Needs attention")

        response = self.client.get(reverse("notification_log"), {"kind": "assigned", "q": "Data Safety"})
        self.assertEqual(len(response.context["notification_rows"]), 2)
        response = self.client.get(reverse("notification_log"), {"q": "Nobody"})
        self.assertContains(response, "No notifications found")


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test",
                   ORG_TIME_ZONE="America/New_York", APP_BASE_URL="http://127.0.0.1:8000")
class NotificationTransitionTests(NotificationFixtures, TestCase):
    def reminder(self, assignment):
        return assignment.notifications.get(kind=Notification.Kind.THREE_DAY_REMINDER)

    def test_suppression_takes_precedence_over_unverified_address(self):
        assignment = self.assign(due_date=(self.today + timedelta(days=10)).isoformat())[0].assignment
        self.alice.verified_email = ""
        self.alice.save()
        cancel_assignment(assignment_id=assignment.id, actor=self.admin, reason="No longer needed", now=self.now)
        assignment.refresh_from_db()
        self.assertEqual(next_state(self.reminder(assignment), assignment, now=self.now),
                         {"status": Notification.Status.SUPPRESSED, "suppression_reason": "assignment_canceled"})

    def test_unverified_address_blocks_delivery(self):
        assignment = self.assign()[0].assignment
        self.alice.verified_email = "old.alice@example.test"
        self.alice.save()
        assignment.refresh_from_db()
        notification = assignment.notifications.get(kind=Notification.Kind.ASSIGNED)
        self.assertEqual(next_state(notification, assignment, now=self.now),
                         {"status": Notification.Status.BLOCKED, "last_error_code": "unverified_address"})

    def test_deliverable_reminder_requeues_at_its_schedule_with_fresh_retry_budget(self):
        assignment = self.assign(due_date=(self.today + timedelta(days=10)).isoformat())[0].assignment
        reminder = self.reminder(assignment)
        reminder.automatic_failures = 3
        self.assertEqual(next_state(reminder, assignment, now=self.now), {
            "status": Notification.Status.PENDING, "next_attempt_at": reminder.scheduled_at,
            "automatic_failures": 0, "last_error_code": "",
        })

    def test_transient_failures_back_off_then_exhaust(self):
        outcome, code, changes = delivery_result(
            Notification(automatic_failures=1), TransientDeliveryError("rate_limited"), now=self.now)
        self.assertEqual((outcome, code), (NotificationDeliveryAttempt.Outcome.TRANSIENT_FAILURE, "rate_limited"))
        self.assertEqual(changes["status"], Notification.Status.RETRY)
        self.assertEqual(changes["next_attempt_at"], self.now + timedelta(seconds=120))
        _, _, changes = delivery_result(Notification(automatic_failures=2), TransientDeliveryError(), now=self.now)
        self.assertEqual(changes["status"], Notification.Status.EXHAUSTED)

    def test_permanent_unknown_and_accepted_outcomes(self):
        cases = [
            (PermanentDeliveryError("Bad Code!"), NotificationDeliveryAttempt.Outcome.PERMANENT_FAILURE,
             "delivery_error", Notification.Status.EXHAUSTED),
            (RuntimeError("boom"), NotificationDeliveryAttempt.Outcome.AMBIGUOUS,
             "unexpected_sender_error", Notification.Status.AMBIGUOUS),
            (None, NotificationDeliveryAttempt.Outcome.ACCEPTED, "", Notification.Status.SENT),
        ]
        for error, outcome, code, status in cases:
            with self.subTest(error=error):
                result = delivery_result(Notification(), error, now=self.now)
                self.assertEqual((result[0], result[1], result[2]["status"]), (outcome, code, status))


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test",
                   ORG_TIME_ZONE="America/New_York")
class NotificationIntentTests(NotificationFixtures, TestCase):
    def test_cancel_and_archive_suppress_followups_immediately(self):
        for ending in ("cancel", "archive"):
            with self.subTest(ending=ending):
                employee = self.alice if ending == "cancel" else self.bob
                assignment = self.assign([employee], due_date=self.today + timedelta(days=5))[0].assignment
                if ending == "cancel":
                    cancel_assignment(assignment_id=assignment.id, actor=self.admin, reason="Withdrawn", now=self.now)
                else:
                    archive_assignment(assignment_id=assignment.id, actor=self.admin, reason="Withdrawn", now=self.now)
                reminders = list(assignment.notifications.exclude(kind=Notification.Kind.ASSIGNED))
                self.assertEqual(len(reminders), 2)
                self.assertEqual({(item.status, item.suppression_reason, item.status_changed_at)
                                  for item in reminders},
                                 {(Notification.Status.SUPPRESSED, "assignment_canceled", self.now)})
                self.assertEqual(assignment.notifications.get(kind=Notification.Kind.ASSIGNED).status,
                                 Notification.Status.PENDING)

    def test_completion_suppresses_reminders_and_preserves_final_or_claimed_rows(self):
        assignment = self.assign(due_date=self.today + timedelta(days=5))[0].assignment
        reminders = list(assignment.notifications.exclude(kind=Notification.Kind.ASSIGNED).order_by("kind"))
        reminders[0].status = Notification.Status.SENT
        reminders[0].save(update_fields=["status"])
        acknowledge_slide(assignment_id=assignment.id, slide_id=self.slide.id, employee=self.alice, now=self.now)
        for reminder in reminders:
            reminder.refresh_from_db()
        self.assertEqual(reminders[0].status, Notification.Status.SENT)
        self.assertEqual((reminders[1].status, reminders[1].suppression_reason),
                         (Notification.Status.SUPPRESSED, "assignment_completed"))

    def test_ending_suppresses_blocked_exhausted_and_manual_nudge_rows(self):
        assignment = self.assign(due_date=self.today + timedelta(days=5))[0].assignment
        reminders = list(assignment.notifications.exclude(kind=Notification.Kind.ASSIGNED).order_by("kind"))
        for reminder, status in zip(reminders, (Notification.Status.BLOCKED, Notification.Status.EXHAUSTED)):
            reminder.status = status
            reminder.save(update_fields=["status"])
        nudge = Notification.objects.create(
            assignment=assignment, kind=Notification.Kind.MANUAL_NUDGE,
            due_revision=assignment.due_revision,
            requested_by=self.admin, requested_at=self.now, next_attempt_at=self.now,
        )
        cancel_assignment(assignment_id=assignment.id, actor=self.admin, reason="Withdrawn", now=self.now)
        self.assertEqual(set(assignment.notifications.filter(status=Notification.Status.SUPPRESSED)
                             .values_list("id", flat=True)), {reminders[0].id, reminders[1].id, nudge.id})
        self.assertEqual(next_manual_nudge_time(assignment, now=self.now), self.now + timedelta(hours=24))

    def test_created_and_deadline_changed_events_use_injected_clock(self):
        assigned_at = self.now - timedelta(hours=2)
        assignment = self.assign(now=assigned_at, due_date=self.today + timedelta(days=5))[0].assignment
        changed_at = self.now + timedelta(minutes=1)
        change_assignment_deadline(assignment_id=assignment.id, actor=self.admin,
                                   due_date_value=self.today + timedelta(days=6), now=changed_at)
        self.assertEqual(assignment.notifications.get(kind=Notification.Kind.ASSIGNED).status_changed_at, assigned_at)
        self.assertEqual(assignment.notifications.get(kind=Notification.Kind.DEADLINE_CHANGED).status_changed_at,
                         changed_at)

    def test_claimed_reminder_is_suppressed_by_worker_after_cancellation(self):
        assignment = self.assign(due_date=self.today + timedelta(days=5))[0].assignment
        reminder = assignment.notifications.get(kind=Notification.Kind.DUE_DAY_REMINDER)
        token = uuid.uuid4()
        Notification.objects.filter(pk=reminder.pk).update(
            status=Notification.Status.SENDING, claim_token=token, claimed_at=self.now,
        )
        cancel_assignment(assignment_id=assignment.id, actor=self.admin, reason="Withdrawn", now=self.now)
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, Notification.Status.SENDING)
        sender = FakeSender([])
        dispatch_claimed(reminder.id, token, now=self.now, sender=sender)
        reminder.refresh_from_db()
        self.assertEqual((reminder.status, reminder.suppression_reason),
                         (Notification.Status.SUPPRESSED, "assignment_canceled"))
        self.assertEqual(sender.calls, [])


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test",
                   ORG_TIME_ZONE="America/New_York")
class FollowUpSummaryTests(NotificationFixtures, TestCase):
    def test_summary_keeps_rolling_limit_and_next_current_reminder(self):
        assignment = self.assign(due_date=self.today + timedelta(days=5))[0].assignment
        requested_at = self.now - timedelta(hours=23)
        Notification.objects.create(
            assignment=assignment, kind=Notification.Kind.MANUAL_NUDGE,
            due_revision=assignment.due_revision,
            requested_by=self.admin, requested_at=requested_at,
            status=Notification.Status.SUPPRESSED, next_attempt_at=requested_at,
        )
        summaries = follow_up_summaries([assignment], now=self.now)
        summary = summaries[assignment.id]
        self.assertEqual(summary.manual_next_at, requested_at + timedelta(hours=24))
        self.assertFalse(summary.manual_can_send)
        self.assertEqual(summary.next_scheduled_reminder, summary.upcoming_reminders[0])
        self.assertEqual(len(summary.upcoming_reminders), 2)
        self.assertEqual(len(summary.notifications), 4)

    def test_batch_uses_one_notification_query_and_independent_summaries(self):
        results = self.assign([self.alice, self.bob], due_date=self.today + timedelta(days=5))
        by_employee = {row.employee_id: row.assignment for row in results}
        assignments = [by_employee[self.alice.id], by_employee[self.bob.id]]
        self.bob.is_active = False
        self.bob.save(update_fields=["is_active"])
        assignments[1].employee = self.bob
        with self.assertNumQueries(1):
            summaries = follow_up_summaries(assignments, now=self.now)
        self.assertTrue(summaries[assignments[0].id].manual_can_send)
        self.assertFalse(summaries[assignments[1].id].manual_can_send)
        self.assertFalse(summaries[assignments[1].id].upcoming_reminders)

    def test_exact_rate_limit_boundary_and_superseded_revision(self):
        assignment = self.assign(due_date=self.today + timedelta(days=5))[0].assignment
        requested_at = self.now - timedelta(hours=24)
        Notification.objects.create(
            assignment=assignment, kind=Notification.Kind.MANUAL_NUDGE,
            due_revision=assignment.due_revision, requested_by=self.admin,
            requested_at=requested_at, next_attempt_at=requested_at,
        )
        change_assignment_deadline(assignment_id=assignment.id, actor=self.admin,
                                   due_date_value=self.today + timedelta(days=6), now=self.now)
        assignment.refresh_from_db()
        summary = follow_up_summaries([assignment], now=self.now)[assignment.id]
        self.assertIsNone(summary.manual_next_at)
        self.assertTrue(summary.manual_can_send)
        self.assertEqual(len(summary.upcoming_reminders), 2)
        self.assertEqual({item.due_revision for item in summary.upcoming_reminders}, {1})


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock behavior")
@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test",
                   ORG_TIME_ZONE="America/New_York")
class ConcurrentNotificationIntentTests(NotificationFixtures, TransactionTestCase):
    def test_deadline_change_and_retry_share_assignment_first_lock_order(self):
        assignment = self.assign(due_date=self.today + timedelta(days=5))[0].assignment
        reminder = assignment.notifications.get(kind=Notification.Kind.DUE_DAY_REMINDER)
        reminder.status = Notification.Status.BLOCKED
        reminder.save(update_fields=["status"])
        barrier = Barrier(2)

        def change():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                change_assignment_deadline(assignment_id=assignment.id, actor=self.admin,
                                           due_date_value=self.today + timedelta(days=6), now=self.now)
            finally:
                connections.close_all()

        def retry():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    retry_notification(notification_id=reminder.id, actor=self.admin, now=self.now)
                except NotificationError as exc:
                    self.assertEqual(str(exc), "Only blocked or exhausted notifications can be retried.")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(change), pool.submit(retry)]
            for future in futures:
                future.result(timeout=20)
        reminder.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(assignment.due_revision, 1)
        self.assertEqual((reminder.status, reminder.suppression_reason),
                         (Notification.Status.SUPPRESSED, "deadline_superseded"))

    def test_cancel_waits_for_claimed_dispatch_and_leaves_no_sending_row(self):
        at_eight = datetime.combine(self.today, time(8), tzinfo=ZoneInfo("America/New_York")).astimezone(utc_timezone.utc)
        at_ten = at_eight + timedelta(hours=2)
        assignment = self.assign(due_date=self.today + timedelta(days=3), now=at_eight)[0].assignment
        reminder = assignment.notifications.get(kind=Notification.Kind.THREE_DAY_REMINDER)
        token = uuid.uuid4()
        Notification.objects.filter(pk=reminder.pk).update(
            status=Notification.Status.SENDING, claim_token=token, claimed_at=at_ten,
        )
        sending = Event()
        release = Event()

        class Sender:
            def send(self, **message):
                sending.set()
                if not release.wait(timeout=10):
                    raise TimeoutError("The dispatch did not resume")
                return "accepted"

        def dispatch():
            close_old_connections()
            try:
                return dispatch_claimed(reminder.id, token, now=at_ten, sender=Sender())
            finally:
                connections.close_all()

        def cancel():
            close_old_connections()
            try:
                return cancel_assignment(assignment_id=assignment.id, actor=self.admin,
                                         reason="Withdrawn", now=at_ten)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            dispatched = pool.submit(dispatch)
            self.assertTrue(sending.wait(timeout=10))
            canceled = pool.submit(cancel)
            release.set()
            dispatched.result(timeout=20)
            canceled.result(timeout=20)
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, Notification.Status.SENT)
        self.assertEqual(reminder.delivery_attempts.count(), 1)
