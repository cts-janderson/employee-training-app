import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone as utc_timezone
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core import mail
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .assignments import acknowledge_slide, cancel_assignment, create_assignments
from .employees import update_demo_employee
from .models import Assignment, Employee, Notification, Slide, Training, TrainingVersion
from .notifications import (
    ManualNudgeIneligible, ManualNudgeRateLimited, NotificationError, TransientDeliveryError,
    process_ready_notifications, request_manual_nudge, retry_notification,
)


def local_utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York")).astimezone(utc_timezone.utc)


class FailingSender:
    def send(self, **kwargs):
        raise TransientDeliveryError("temporary_failure")


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test",
                   ORG_TIME_ZONE="America/New_York", APP_BASE_URL="http://127.0.0.1:8000")
class ManualNudgeTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(name="Admin", email="demo.admin@example.test",
                                             verified_email="demo.admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.other_admin = Employee.objects.create(name="Other admin", email="other.admin@example.test",
                                                   verified_email="other.admin@example.test",
                                                   role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.employee = Employee.objects.create(name="Alice", email="alice@example.test",
                                                verified_email="alice@example.test", is_demo=True)
        self.training = Training.objects.create()
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        Slide.objects.create(version=self.version, position=1, header="Read", body="Material")
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.published_at = local_utc(2026, 3, 5, 8)
        self.version.save()
        self.start = local_utc(2026, 3, 5, 8)
        result = create_assignments(operation_id=uuid.uuid4(), actor=self.admin,
                                    version_id=self.version.id, employee_ids=[self.employee.id],
                                    due_date_value=date(2026, 3, 8), now=self.start)[0]
        self.assignment = result.assignment
        self.assignment.notifications.filter(kind=Notification.Kind.ASSIGNED).update(
            status=Notification.Status.SENT, sent_at=self.start,
        )

    def manual(self, *, actor=None, now=None):
        return request_manual_nudge(assignment_id=self.assignment.id, actor=actor or self.admin,
                                    now=now or self.start)

    def test_request_audits_actor_and_time_then_worker_records_delivery(self):
        nudge = self.manual()
        self.assertEqual(nudge.status, Notification.Status.PENDING)
        self.assertEqual(nudge.requested_by, self.admin)
        self.assertEqual(nudge.requested_at, self.start)
        self.assertFalse(nudge.delivery_attempts.exists())
        queued_page = self.client.get(reverse("notification_detail", args=[nudge.id]))
        self.assertContains(queued_page, "Queued for delivery")
        self.assertNotContains(queued_page, "Processed")
        self.assertEqual(len(process_ready_notifications(now=self.start)), 1)
        nudge.refresh_from_db()
        self.assertEqual(nudge.status, Notification.Status.SENT)
        self.assertEqual(nudge.delivery_attempts.get().recipient_email, self.employee.email)
        sent_page = self.client.get(reverse("notification_detail", args=[nudge.id]))
        self.assertContains(sent_page, "Processed")
        self.assertContains(self.client.get(reverse("notification_log")), "Processed")
        self.assertIn("manual reminder", mail.outbox[0].subject)
        self.assertEqual(self.assignment.notifications.filter(kind=Notification.Kind.MANUAL_NUDGE).count(), 1)

    def test_rolling_limit_crosses_administrators_and_resets_at_exactly_24_hours(self):
        first = self.manual()
        with self.assertRaises(ManualNudgeRateLimited) as caught:
            self.manual(actor=self.other_admin, now=self.start + timedelta(hours=23, minutes=59))
        self.assertEqual(caught.exception.next_eligible_at, self.start + timedelta(hours=24))
        second = self.manual(actor=self.other_admin, now=self.start + timedelta(hours=24))
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(second.requested_by, self.other_admin)
        self.assertEqual(self.assignment.notifications.filter(kind=Notification.Kind.MANUAL_NUDGE).count(), 2)

    def test_failed_delivery_retry_uses_same_request_and_allowance(self):
        nudge = self.manual()
        process_ready_notifications(now=self.start, sender=FailingSender())
        nudge.refresh_from_db()
        self.assertEqual(nudge.status, Notification.Status.RETRY)
        with self.assertRaises(ManualNudgeRateLimited):
            self.manual(now=self.start + timedelta(hours=1))
        Notification.objects.filter(pk=nudge.id).update(status=Notification.Status.EXHAUSTED)
        retry_notification(notification_id=nudge.id, actor=self.admin, now=self.start + timedelta(hours=2))
        process_ready_notifications(now=self.start + timedelta(hours=2))
        nudge.refresh_from_db()
        self.assertEqual(nudge.status, Notification.Status.SENT)
        self.assertEqual(nudge.delivery_attempts.count(), 2)
        self.assertEqual(self.assignment.notifications.filter(kind=Notification.Kind.MANUAL_NUDGE).count(), 1)

    def test_manual_nudge_does_not_change_scheduled_reminder(self):
        scheduled = self.assignment.notifications.get(kind=Notification.Kind.THREE_DAY_REMINDER)
        manual_time = local_utc(2026, 3, 5, 8, 59)
        nudge = self.manual(now=manual_time)
        self.assertEqual(len(process_ready_notifications(now=manual_time)), 1)
        scheduled.refresh_from_db()
        self.assertEqual(scheduled.status, Notification.Status.PENDING)
        self.assertEqual(scheduled.scheduled_at, local_utc(2026, 3, 5, 9))
        self.assertEqual(len(process_ready_notifications(now=scheduled.scheduled_at)), 1)
        scheduled.refresh_from_db()
        self.assertEqual(scheduled.status, Notification.Status.SENT)
        self.assertEqual(nudge.requested_at, manual_time)
        self.assertEqual(len(mail.outbox), 2)

    def test_ineligible_assignments_and_inactive_recipient_are_rejected_or_suppressed(self):
        nudge = self.manual()
        update_demo_employee(employee_id=self.employee.id, name=self.employee.name,
                             email=self.employee.email, active=False, actor=self.admin,
                             now=self.start + timedelta(minutes=1))
        with self.assertRaises(ManualNudgeIneligible):
            self.manual(now=self.start + timedelta(minutes=2))
        process_ready_notifications(now=self.start + timedelta(minutes=2))
        nudge.refresh_from_db()
        self.assertEqual(nudge.suppression_reason, "employee_inactive")
        update_demo_employee(employee_id=self.employee.id, name=self.employee.name,
                             email=self.employee.email, active=True, actor=self.admin,
                             now=self.start + timedelta(minutes=3))
        cancel_assignment(assignment_id=self.assignment.id, actor=self.admin, reason="No longer needed",
                          now=self.start + timedelta(minutes=4))
        with self.assertRaises(ManualNudgeIneligible):
            self.manual(now=self.start + timedelta(hours=25))
        with self.assertRaises(NotificationError):
            self.manual(actor=self.employee, now=self.start + timedelta(hours=25))

    def test_completion_suppresses_queued_nudge_and_rejects_new_request(self):
        nudge = self.manual()
        acknowledge_slide(assignment_id=self.assignment.id, slide_id=self.version.slides.get().id,
                          employee=self.employee, now=self.start + timedelta(minutes=1))
        process_ready_notifications(now=self.start + timedelta(minutes=2))
        nudge.refresh_from_db()
        self.assertEqual(nudge.suppression_reason, "assignment_completed")
        with self.assertRaises(ManualNudgeIneligible):
            self.manual(now=self.start + timedelta(hours=25))

    def test_cancellation_suppresses_queued_nudge(self):
        nudge = self.manual()
        cancel_assignment(assignment_id=self.assignment.id, actor=self.admin,
                          reason="No longer needed", now=self.start + timedelta(minutes=1))
        process_ready_notifications(now=self.start + timedelta(minutes=2))
        nudge.refresh_from_db()
        self.assertEqual(nudge.suppression_reason, "assignment_canceled")

    def test_overdue_incomplete_assignment_can_receive_manual_nudge(self):
        overdue = local_utc(2026, 3, 9, 10)
        nudge = self.manual(now=overdue)
        process_ready_notifications(now=overdue)
        nudge.refresh_from_db()
        self.assertEqual(nudge.status, Notification.Status.SENT)

    def test_dashboard_detail_post_and_rate_limit_show_queue_and_next_time(self):
        with patch("training.assignment_admin_views.timezone.now", return_value=self.start):
            dashboard = self.client.get(reverse("assignment_dashboard"))
            detail = self.client.get(reverse("assignment_admin_detail", args=[self.assignment.id]))
        self.assertContains(dashboard, "Send reminder")
        self.assertContains(dashboard, "Next reminder:")
        self.assertContains(detail, "Next scheduled reminder:")
        with patch("training.notifications.timezone.now", return_value=self.start):
            response = self.client.post(reverse("send_manual_nudge", args=[self.assignment.id]))
        self.assertEqual(response.status_code, 302)
        nudge = self.assignment.notifications.get(kind=Notification.Kind.MANUAL_NUDGE)
        self.assertEqual(response.url, reverse("notification_detail", args=[nudge.id]))
        notification_page = self.client.get(response.url)
        self.assertContains(notification_page, "This message is waiting to be processed.")
        self.assertContains(notification_page, "Requested by Admin")
        with patch("training.notifications.timezone.now", return_value=self.start + timedelta(hours=1)), \
             patch("training.assignment_admin_views.timezone.now", return_value=self.start + timedelta(hours=1)):
            repeat = self.client.post(reverse("send_manual_nudge", args=[self.assignment.id]))
        self.assertEqual(repeat.status_code, 429)
        self.assertContains(repeat, "You can send another reminder after", status_code=429)
        with patch("training.assignment_admin_views.timezone.now", return_value=self.start + timedelta(hours=1)):
            limited_dashboard = self.client.get(reverse("assignment_dashboard"))
        self.assertContains(limited_dashboard, "Another reminder is available after")
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        self.assertEqual(self.client.post(reverse("send_manual_nudge", args=[self.assignment.id])).status_code, 403)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock behavior")
@override_settings(DEBUG=True, DEV_AUTH_STUB=True)
class ConcurrentManualNudgeTests(TransactionTestCase):
    def test_concurrent_administrators_queue_only_one_request(self):
        admins = [Employee.objects.create(name=f"Admin {number}", email=f"admin{number}@example.test",
                                          role=Employee.Role.ADMINISTRATOR, is_demo=True)
                  for number in (1, 2)]
        employee = Employee.objects.create(name="Alice", email="alice@example.test", is_demo=True)
        training = Training.objects.create()
        version = TrainingVersion.objects.create(training=training, version_number=1, title="Safety")
        assignment = Assignment.objects.create(
            employee=employee, training=training, version=version,
            due_date=date(2026, 3, 8), due_timezone="America/New_York",
            due_at=local_utc(2026, 3, 9, 0),
        )
        barrier = Barrier(2)
        now = local_utc(2026, 3, 5, 8)

        def submit(actor):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    request_manual_nudge(assignment_id=assignment.id, actor=actor, now=now)
                except ManualNudgeRateLimited:
                    return "rate_limited"
                return "queued"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, admins))
        self.assertCountEqual(results, ["queued", "rate_limited"])
        self.assertEqual(Notification.objects.filter(assignment=assignment,
                                                     kind=Notification.Kind.MANUAL_NUDGE).count(), 1)
