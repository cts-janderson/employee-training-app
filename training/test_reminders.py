import uuid
from importlib import import_module
from datetime import date, datetime, timedelta, timezone as utc_timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.apps import apps
from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from .assignments import acknowledge_slide, cancel_assignment, change_assignment_deadline, create_assignments
from .employees import update_employee
from .models import Employee, Notification, Slide, Training, TrainingVersion
from .notifications import (
    TransientDeliveryError, claim_next_notification, process_ready_notifications,
    reconcile_notification, recover_stale_claims, reminder_times, retry_notification,
    schedule_reminders,
)


def local_utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York")).astimezone(utc_timezone.utc)


class FailingSender:
    def __init__(self):
        self.calls = 0

    def send(self, **kwargs):
        self.calls += 1
        raise TransientDeliveryError("temporary_failure")


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test",
                   ORG_TIME_ZONE="America/New_York", APP_BASE_URL="http://127.0.0.1:8000")
class ReminderWorkflowTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(name="Admin", email="demo.admin@example.test",
                                             verified_email="demo.admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.alice = self.employee("Alice")
        self.bob = self.employee("Bob")
        self.carol = self.employee("Carol")
        self.training = Training.objects.create(is_demo=True)
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        self.slide = Slide.objects.create(version=self.version, position=1, header="Read", body="Material")
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.published_at = local_utc(2026, 3, 5, 8)
        self.version.save()
        self.start = local_utc(2026, 3, 5, 8)

    def employee(self, name):
        email = f"{name.lower()}@example.test"
        return Employee.objects.create(name=name, email=email, verified_email=email, is_demo=True)

    def assign(self, employee, *, due_date=date(2026, 3, 8), now=None):
        result = create_assignments(
            operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
            employee_ids=[employee.id], due_date_value=due_date, now=now or self.start,
        )[0]
        self.assertEqual(result.outcome, "created")
        return result.assignment

    def reminder(self, assignment, kind, revision=0):
        return assignment.notifications.get(kind=kind, due_revision=revision)

    def mark_assignment_message_sent(self, assignment):
        assignment.notifications.filter(kind=Notification.Kind.ASSIGNED).update(
            status=Notification.Status.SENT, sent_at=self.start,
        )

    def activity(self, employee, active, at):
        return update_employee(
            employee_id=employee.id, name=employee.name, email=employee.email,
            active=active, actor=self.admin, now=at,
        )

    def test_dst_schedule_and_both_reminders_send_at_local_nine(self):
        spring = reminder_times(date(2026, 3, 8), "America/New_York")
        self.assertEqual(spring[Notification.Kind.THREE_DAY_REMINDER], datetime(2026, 3, 5, 14, tzinfo=utc_timezone.utc))
        self.assertEqual(spring[Notification.Kind.DUE_DAY_REMINDER], datetime(2026, 3, 8, 13, tzinfo=utc_timezone.utc))
        fall = reminder_times(date(2026, 11, 1), "America/New_York")
        self.assertEqual(fall[Notification.Kind.THREE_DAY_REMINDER], datetime(2026, 10, 29, 13, tzinfo=utc_timezone.utc))
        self.assertEqual(fall[Notification.Kind.DUE_DAY_REMINDER], datetime(2026, 11, 1, 14, tzinfo=utc_timezone.utc))
        assignment = self.assign(self.alice)
        self.assertEqual(self.reminder(assignment, Notification.Kind.THREE_DAY_REMINDER).scheduled_at, spring[Notification.Kind.THREE_DAY_REMINDER])
        self.assertEqual(self.reminder(assignment, Notification.Kind.DUE_DAY_REMINDER).scheduled_at, spring[Notification.Kind.DUE_DAY_REMINDER])
        self.assertEqual(len(process_ready_notifications(now=self.start)), 1)
        self.assertEqual(len(process_ready_notifications(now=spring[Notification.Kind.THREE_DAY_REMINDER] - timedelta(microseconds=1))), 0)
        at_three = process_ready_notifications(now=spring[Notification.Kind.THREE_DAY_REMINDER])
        self.assertEqual([item.kind for item in at_three], [Notification.Kind.THREE_DAY_REMINDER])
        self.assertEqual(len(process_ready_notifications(now=spring[Notification.Kind.DUE_DAY_REMINDER] - timedelta(microseconds=1))), 0)
        at_due = process_ready_notifications(now=spring[Notification.Kind.DUE_DAY_REMINDER])
        self.assertEqual([item.kind for item in at_due], [Notification.Kind.DUE_DAY_REMINDER])
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(len(process_ready_notifications(now=spring[Notification.Kind.DUE_DAY_REMINDER])), 0)
        self.assertIn("reminder: due today", self.reminder(assignment, Notification.Kind.DUE_DAY_REMINDER).delivery_attempts.get().subject)

    def test_assignments_inside_window_and_due_day_record_skipped_times(self):
        inside = self.assign(self.alice, now=local_utc(2026, 3, 6, 10))
        self.assertEqual((self.reminder(inside, Notification.Kind.THREE_DAY_REMINDER).status,
                          self.reminder(inside, Notification.Kind.THREE_DAY_REMINDER).suppression_reason),
                         (Notification.Status.SUPPRESSED, "schedule_elapsed"))
        self.assertEqual(self.reminder(inside, Notification.Kind.DUE_DAY_REMINDER).status, Notification.Status.PENDING)
        due_morning = self.assign(self.bob, now=local_utc(2026, 3, 8, 8, 30))
        self.assertEqual(self.reminder(due_morning, Notification.Kind.DUE_DAY_REMINDER).status, Notification.Status.PENDING)
        due_late = self.assign(self.carol, now=local_utc(2026, 3, 8, 10))
        self.assertEqual(self.reminder(due_late, Notification.Kind.DUE_DAY_REMINDER).suppression_reason, "schedule_elapsed")
        self.assertEqual(due_late.notifications.filter(kind__in=[
            Notification.Kind.THREE_DAY_REMINDER, Notification.Kind.DUE_DAY_REMINDER,
        ]).count(), 2)
        self.assertEqual(due_late.notifications.filter(status=Notification.Status.PENDING).count(), 1)

    def test_migration_schedules_future_reminders_for_existing_assignments(self):
        assignment = self.assign(self.alice)
        assignment.notifications.filter(kind__in=[
            Notification.Kind.THREE_DAY_REMINDER, Notification.Kind.DUE_DAY_REMINDER,
        ]).delete()
        migration = import_module("training.migrations.0007_notification_scheduled_at_alter_notification_kind_and_more")
        now = local_utc(2026, 3, 6, 10)
        with patch.object(migration.timezone, "now", return_value=now):
            migration.schedule_existing_assignment_reminders(apps, SimpleNamespace(connection=connection))
            migration.schedule_existing_assignment_reminders(apps, SimpleNamespace(connection=connection))
        self.assertEqual(self.reminder(assignment, Notification.Kind.THREE_DAY_REMINDER).suppression_reason,
                         "schedule_elapsed")
        self.assertEqual(self.reminder(assignment, Notification.Kind.DUE_DAY_REMINDER).status,
                         Notification.Status.PENDING)
        self.assertEqual(assignment.notifications.filter(kind=Notification.Kind.DUE_DAY_REMINDER).count(), 1)

    def test_outage_between_reminder_times_sends_three_day_reminder(self):
        three = self.assign(self.alice)
        self.mark_assignment_message_sent(three)
        process_ready_notifications(now=local_utc(2026, 3, 6, 12))
        self.assertEqual(self.reminder(three, Notification.Kind.THREE_DAY_REMINDER).status, Notification.Status.SENT)
        self.assertEqual(self.reminder(three, Notification.Kind.DUE_DAY_REMINDER).status, Notification.Status.PENDING)

    def test_outage_until_late_due_day_sends_only_latest_reminder(self):
        due = self.assign(self.bob)
        self.mark_assignment_message_sent(due)
        process_ready_notifications(now=local_utc(2026, 3, 8, 12))
        self.assertEqual(self.reminder(due, Notification.Kind.THREE_DAY_REMINDER).suppression_reason, "reminder_superseded")
        self.assertEqual(self.reminder(due, Notification.Kind.DUE_DAY_REMINDER).status, Notification.Status.SENT)

    def test_outage_past_deadline_expires_both_reminders(self):
        expired = self.assign(self.carol)
        self.mark_assignment_message_sent(expired)
        process_ready_notifications(now=local_utc(2026, 3, 9, 0))
        self.assertEqual(self.reminder(expired, Notification.Kind.THREE_DAY_REMINDER).suppression_reason, "reminder_expired")
        self.assertEqual(self.reminder(expired, Notification.Kind.DUE_DAY_REMINDER).suppression_reason, "reminder_expired")

    def test_deadline_changes_invalidate_unsent_reminders_and_preserve_sent_history(self):
        assignment = self.assign(self.alice, due_date=date(2026, 3, 10))
        self.mark_assignment_message_sent(assignment)
        old_three = self.reminder(assignment, Notification.Kind.THREE_DAY_REMINDER)
        old_due = self.reminder(assignment, Notification.Kind.DUE_DAY_REMINDER)
        self.assertEqual(old_three.status, Notification.Status.PENDING)
        change_assignment_deadline(assignment_id=assignment.id, actor=self.admin,
                                   due_date_value=date(2026, 3, 8), now=self.start)
        old_three.refresh_from_db()
        old_due.refresh_from_db()
        self.assertEqual((old_three.suppression_reason, old_due.suppression_reason),
                         ("deadline_superseded", "deadline_superseded"))
        self.assertEqual(self.reminder(assignment, Notification.Kind.THREE_DAY_REMINDER, 1).status, Notification.Status.PENDING)
        change_assignment_deadline(assignment_id=assignment.id, actor=self.admin,
                                   due_date_value=date(2026, 3, 12), now=local_utc(2026, 3, 5, 10))
        self.assertEqual(self.reminder(assignment, Notification.Kind.DUE_DAY_REMINDER, 1).suppression_reason, "deadline_superseded")
        self.assertEqual(self.reminder(assignment, Notification.Kind.THREE_DAY_REMINDER, 2).status, Notification.Status.PENDING)
        self.assertEqual(len(schedule_reminders(assignment, now=self.start)), 2)
        self.assertEqual(assignment.notifications.filter(kind=Notification.Kind.THREE_DAY_REMINDER, due_revision=2).count(), 1)
        # An accepted old reminder remains in history even after a new deadline is selected.
        second = self.assign(self.bob, due_date=date(2026, 3, 8))
        self.mark_assignment_message_sent(second)
        process_ready_notifications(now=local_utc(2026, 3, 5, 9))
        sent = self.reminder(second, Notification.Kind.THREE_DAY_REMINDER)
        self.assertEqual(sent.status, Notification.Status.SENT)
        change_assignment_deadline(assignment_id=second.id, actor=self.admin,
                                   due_date_value=date(2026, 3, 12), now=local_utc(2026, 3, 5, 10))
        sent.refresh_from_db()
        self.assertEqual(sent.status, Notification.Status.SENT)
        self.assertEqual(sent.delivery_attempts.count(), 1)

    def test_completion_cancellation_and_retry_recheck_eligibility(self):
        alice_assignment = self.assign(self.alice)
        bob_assignment = self.assign(self.bob)
        carol_assignment = self.assign(self.carol)
        for assignment in [alice_assignment, bob_assignment, carol_assignment]:
            self.mark_assignment_message_sent(assignment)
        failed = FailingSender()
        process_ready_notifications(now=local_utc(2026, 3, 5, 9), sender=failed)
        self.assertEqual(failed.calls, 3)
        acknowledge_slide(assignment_id=alice_assignment.id, slide_id=self.slide.id,
                          employee=self.alice, now=local_utc(2026, 3, 5, 9, 30))
        cancel_assignment(assignment_id=bob_assignment.id, actor=self.admin,
                          reason="Withdrawn", now=local_utc(2026, 3, 5, 9, 30))
        self.activity(self.carol, False, local_utc(2026, 3, 5, 9, 30))
        process_ready_notifications(now=local_utc(2026, 3, 5, 10))
        self.assertEqual(self.reminder(alice_assignment, Notification.Kind.THREE_DAY_REMINDER).suppression_reason, "assignment_completed")
        self.assertEqual(self.reminder(bob_assignment, Notification.Kind.THREE_DAY_REMINDER).suppression_reason, "assignment_canceled")
        self.assertEqual(self.reminder(carol_assignment, Notification.Kind.THREE_DAY_REMINDER).suppression_reason, "employee_inactive")
        self.assertEqual(failed.calls, 3)

    def test_retry_after_due_day_or_deadline_is_suppressed_for_auto_and_admin(self):
        assignment = self.assign(self.alice)
        self.mark_assignment_message_sent(assignment)
        failed = FailingSender()
        three_time = local_utc(2026, 3, 5, 9)
        due_time = local_utc(2026, 3, 8, 9)
        deadline = local_utc(2026, 3, 9, 0)
        process_ready_notifications(now=three_time, sender=failed)
        self.assertEqual(self.reminder(assignment, Notification.Kind.THREE_DAY_REMINDER).status, Notification.Status.RETRY)
        process_ready_notifications(now=due_time, sender=failed)
        self.assertEqual(self.reminder(assignment, Notification.Kind.THREE_DAY_REMINDER).suppression_reason, "reminder_superseded")
        due_reminder = self.reminder(assignment, Notification.Kind.DUE_DAY_REMINDER)
        self.assertEqual(due_reminder.status, Notification.Status.RETRY)
        process_ready_notifications(now=deadline, sender=failed)
        self.assertEqual(self.reminder(assignment, Notification.Kind.DUE_DAY_REMINDER).suppression_reason, "reminder_expired")
        self.assertEqual(failed.calls, 2)
        # An administrator retry of an exhausted earlier reminder follows the same checks.
        another = self.assign(self.bob)
        self.mark_assignment_message_sent(another)
        three = self.reminder(another, Notification.Kind.THREE_DAY_REMINDER)
        Notification.objects.filter(pk=three.id).update(status=Notification.Status.EXHAUSTED)
        retry_notification(notification_id=three.id, actor=self.admin, now=due_time)
        three.refresh_from_db()
        self.assertEqual(three.suppression_reason, "reminder_superseded")
        due = self.reminder(another, Notification.Kind.DUE_DAY_REMINDER)
        Notification.objects.filter(pk=due.id).update(status=Notification.Status.EXHAUSTED)
        retry_notification(notification_id=due.id, actor=self.admin, now=deadline)
        due.refresh_from_db()
        self.assertEqual(due.suppression_reason, "reminder_expired")

    def test_reactivation_before_and_after_schedule_only_keeps_future_reminders(self):
        before = self.assign(self.alice)
        after = self.assign(self.bob)
        during = self.assign(self.carol)
        self.mark_assignment_message_sent(before)
        self.mark_assignment_message_sent(after)
        self.mark_assignment_message_sent(during)
        self.activity(self.alice, False, local_utc(2026, 3, 5, 8, 15))
        self.activity(self.alice, True, local_utc(2026, 3, 5, 8, 45))
        self.activity(self.bob, False, local_utc(2026, 3, 5, 8, 15))
        self.activity(self.bob, True, local_utc(2026, 3, 5, 10))
        self.activity(self.carol, False, local_utc(2026, 3, 5, 9, 15))
        self.activity(self.carol, True, local_utc(2026, 3, 5, 10))
        process_ready_notifications(now=local_utc(2026, 3, 5, 10, 5))
        self.assertEqual(self.reminder(before, Notification.Kind.THREE_DAY_REMINDER).status, Notification.Status.SENT)
        self.assertEqual(self.reminder(after, Notification.Kind.THREE_DAY_REMINDER).suppression_reason,
                         "employee_inactive_during_window")
        self.assertEqual(self.reminder(during, Notification.Kind.THREE_DAY_REMINDER).suppression_reason,
                         "employee_inactive_during_window")
        self.assertEqual(self.reminder(after, Notification.Kind.DUE_DAY_REMINDER).status, Notification.Status.PENDING)
        process_ready_notifications(now=local_utc(2026, 3, 8, 9))
        self.assertEqual(self.reminder(after, Notification.Kind.DUE_DAY_REMINDER).status, Notification.Status.SENT)
        self.assertEqual(after.notifications.filter(kind=Notification.Kind.THREE_DAY_REMINDER).count(), 1)

    def test_reactivation_of_preexisting_inactive_employee_does_not_send_missed_reminder(self):
        assignment = self.assign(self.alice)
        self.mark_assignment_message_sent(assignment)
        Employee.objects.filter(pk=self.alice.pk).update(is_active=False)
        self.activity(self.alice, True, local_utc(2026, 3, 5, 10))
        process_ready_notifications(now=local_utc(2026, 3, 5, 10, 5))
        self.assertEqual(self.reminder(assignment, Notification.Kind.THREE_DAY_REMINDER).suppression_reason,
                         "employee_inactive_during_window")
        self.assertEqual(self.reminder(assignment, Notification.Kind.DUE_DAY_REMINDER).status,
                         Notification.Status.PENDING)

    def test_worker_restart_and_replay_keep_one_reminder_per_revision(self):
        assignment = self.assign(self.alice)
        self.mark_assignment_message_sent(assignment)
        three_time = local_utc(2026, 3, 5, 9)
        claim = claim_next_notification(now=three_time)
        self.assertEqual(claim[0], self.reminder(assignment, Notification.Kind.THREE_DAY_REMINDER).id)
        self.assertIsNone(claim_next_notification(now=three_time))
        self.assertEqual(recover_stale_claims(now=three_time + timedelta(seconds=301)), 1)
        reminder = self.reminder(assignment, Notification.Kind.THREE_DAY_REMINDER)
        self.assertEqual(reminder.status, Notification.Status.AMBIGUOUS)
        reconcile_notification(notification_id=reminder.id, actor=self.admin, decision="not_delivered",
                               reason="Worker stopped before dispatch", now=three_time + timedelta(seconds=302))
        self.assertEqual(len(process_ready_notifications(now=three_time + timedelta(seconds=303))), 1)
        self.assertEqual(self.reminder(assignment, Notification.Kind.THREE_DAY_REMINDER).status, Notification.Status.SENT)
        schedule_reminders(assignment, now=three_time)
        self.assertEqual(assignment.notifications.filter(kind=Notification.Kind.THREE_DAY_REMINDER).count(), 1)
        self.assertEqual(self.reminder(assignment, Notification.Kind.THREE_DAY_REMINDER).delivery_attempts.count(), 1)

    def test_admin_detail_shows_upcoming_and_history_without_employee_access(self):
        assignment = self.assign(self.alice)
        with patch("training.assignment_admin_views.timezone.now", return_value=self.start):
            detail = self.client.get(reverse("assignment_admin_detail", args=[assignment.id]))
        self.assertEqual(len(detail.context["upcoming_reminders"]), 2)
        self.assertContains(detail, "Upcoming reminders")
        self.assertContains(detail, "Three-day reminder")
        self.assertContains(detail, "Due-day reminder")
        self.assertContains(detail, "Notification history")
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        self.assertEqual(self.client.get(reverse("assignment_admin_detail", args=[assignment.id])).status_code, 403)
