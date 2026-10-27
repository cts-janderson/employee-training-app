import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignments import AssignmentAccessError, acknowledge_slide, archive_assignment, create_assignments, restore_assignment
from .models import Assignment, AssignmentHistory, Employee, Notification, Slide, Training, TrainingVersion
from .notifications import process_ready_notifications


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test",
                   ORG_TIME_ZONE="America/New_York", APP_BASE_URL="http://127.0.0.1:8000")
class AssignmentArchiveTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(
            name="Admin", email="demo.admin@example.test", verified_email="demo.admin@example.test",
            role=Employee.Role.ADMINISTRATOR, is_demo=True,
        )
        self.employee = Employee.objects.create(
            name="Learner", email="learner@example.test", verified_email="learner@example.test", is_demo=True,
        )
        self.training = Training.objects.create()
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        self.slide = Slide.objects.create(version=self.version, position=1, header="Read", body="Material")
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.published_at = timezone.now()
        self.version.save()
        self.now = timezone.now()
        self.due_date = (timezone.localdate() + timedelta(days=2)).isoformat()
        create_assignments(
            operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
            employee_ids=[self.employee.id], due_date_value=self.due_date, now=self.now,
        )
        self.assignment = Assignment.objects.get(employee=self.employee)

    def test_pending_archive_cancels_once_preserves_evidence_and_allows_replacement(self):
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        self.client.get(reverse("assignment_slide", args=[self.assignment.id, self.slide.id]))
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.admin.id)})
        archive_url = reverse("archive_assignment", args=[self.assignment.id])
        self.assertEqual(self.client.post(archive_url, {"reason": "Assigned in error"}).status_code, 302)
        self.assignment.refresh_from_db()
        self.assertIsNotNone(self.assignment.canceled_at)
        self.assertIsNotNone(self.assignment.archived_at)
        self.assertIsNotNone(self.assignment.started_at)
        self.assertEqual(set(self.assignment.history.values_list("kind", flat=True)),
                         {AssignmentHistory.Kind.ARCHIVED, AssignmentHistory.Kind.CANCELED})
        self.assertEqual(self.client.post(archive_url, {"reason": "Repeat"}).status_code, 302)
        self.assertEqual(self.assignment.history.count(), 2)
        self.assertNotContains(self.client.get(reverse("assignment_dashboard")), "Learner · Safety")
        archived = self.client.get(reverse("assignment_dashboard"), {"state": "archived"})
        self.assertContains(archived, "Learner · Safety")
        self.assertContains(archived, "Archived · Canceled")
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        self.assertNotContains(self.client.get(reverse("my_training")), "Safety")
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.admin.id)})
        revised = TrainingVersion.objects.create(training=self.training, version_number=2, title="Revised Safety")
        Slide.objects.create(version=revised, position=1, header="New", body="New material")
        revised.status = TrainingVersion.Status.PUBLISHED
        revised.save()
        replacement = create_assignments(
            operation_id=uuid.uuid4(), actor=self.admin, version_id=revised.id,
            employee_ids=[self.employee.id], due_date_value=self.due_date, now=self.now,
        )
        self.assertEqual(replacement[0].outcome, "created")
        self.assertEqual(Assignment.objects.filter(employee=self.employee).count(), 2)
        process_ready_notifications(now=self.now + timedelta(minutes=1))
        self.assertFalse(self.assignment.notifications.filter(status=Notification.Status.SENT).exists())
        self.assertEqual(self.client.post(reverse("restore_assignment", args=[self.assignment.id])).status_code, 302)
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.archived_at)
        self.assertIsNotNone(self.assignment.canceled_at)
        self.assertEqual(self.assignment.history.filter(kind=AssignmentHistory.Kind.RESTORED).count(), 1)
        self.assertEqual(self.client.post(reverse("restore_assignment", args=[self.assignment.id])).status_code, 302)
        self.assertEqual(self.assignment.history.filter(kind=AssignmentHistory.Kind.RESTORED).count(), 1)

    def test_completed_archive_hides_list_but_preserves_direct_review_and_restores(self):
        acknowledge_slide(assignment_id=self.assignment.id, slide_id=self.slide.id,
                          employee=self.employee, now=self.now)
        self.assignment.refresh_from_db()
        self.assertIsNotNone(self.assignment.completed_at)
        self.assertEqual(self.client.post(reverse("archive_assignment", args=[self.assignment.id]),
                                          {"reason": "Old completed work"}).status_code, 302)
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.canceled_at)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        self.assertNotContains(self.client.get(reverse("my_training")), "Safety")
        self.assertContains(self.client.get(reverse("assignment_detail", args=[self.assignment.id])), "Training complete")
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.admin.id)})
        self.client.post(reverse("restore_assignment", args=[self.assignment.id]))
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        self.assertContains(self.client.get(reverse("my_training")), "Safety")

    def test_archive_requires_reason_and_admin_role(self):
        url = reverse("archive_assignment", args=[self.assignment.id])
        self.assertContains(self.client.post(url, {"reason": " "}), "Enter an archive reason", status_code=400)
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.archived_at)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        self.assertEqual(self.client.post(url, {"reason": "Unauthorized"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("restore_assignment", args=[self.assignment.id])).status_code, 403)

    def test_restore_clears_archived_at_and_records_restoration(self):
        archived = archive_assignment(assignment_id=self.assignment.id, actor=self.admin,
                                      reason="Old", now=self.now)
        restored = restore_assignment(assignment_id=archived.id, actor=self.admin, now=self.now + timedelta(hours=1))
        self.assertIsNone(restored.archived_at)
        event = restored.history.get(kind=AssignmentHistory.Kind.RESTORED)
        self.assertEqual((event.old_archived_at, event.new_archived_at), (self.now, None))

    def test_restore_of_unarchived_assignment_changes_nothing(self):
        restored = restore_assignment(assignment_id=self.assignment.id, actor=self.admin, now=self.now)
        self.assertEqual(restored.id, self.assignment.id)
        self.assertFalse(restored.history.exists())

    def test_restore_does_not_reopen_a_canceled_obligation(self):
        archive_assignment(assignment_id=self.assignment.id, actor=self.admin, reason="Old", now=self.now)
        restored = restore_assignment(assignment_id=self.assignment.id, actor=self.admin, now=self.now)
        self.assertIsNotNone(restored.canceled_at)
        self.assertEqual(restored.ending, "canceled")
        self.assertFalse(Assignment.objects.visible_to_learner(self.employee).filter(pk=restored.id).exists())

    def test_restore_requires_administrator(self):
        with self.assertRaisesMessage(AssignmentAccessError, "Administrator access is required."):
            restore_assignment(assignment_id=self.assignment.id, actor=self.employee, now=self.now)
