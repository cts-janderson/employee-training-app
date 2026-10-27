import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignment_reports import ReportFilters, export_rows, report_assignments, report_rows
from .assignments import AssignmentAccessError, AssignmentConflict, AssignmentInputError, create_assignments, waive_assignment
from .models import Assignment, Employee, Notification, Slide, Training, TrainingVersion


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test", ORG_TIME_ZONE="UTC")
class WaiverTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(name="Admin", email="demo.admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.employee = Employee.objects.create(name="Alice", email="alice@example.test", is_demo=True)
        self.training = Training.objects.create()
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        self.slide = Slide.objects.create(version=self.version, position=1, header="Read", body="Read")
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.save()
        self.now = timezone.now()
        self.due = (self.now + timedelta(days=3)).date().isoformat()
        create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                           employee_ids=[self.employee.id], due_date_value=self.due, now=self.now)
        self.assignment = Assignment.objects.get(employee=self.employee)

    def test_waiver_ends_obligation_suppresses_reminders_and_allows_replacement(self):
        due_at = self.assignment.due_at
        waived, history = waive_assignment(assignment_id=self.assignment.id, actor=self.admin,
                                           reason="No longer required", now=self.now)
        self.assertEqual(history.reason, "No longer required")
        self.assertIsNone(waived.completed_at)
        self.assertEqual(waived.due_at, due_at)
        self.assertFalse(Assignment.objects.outstanding().filter(pk=waived.id).exists())
        reminder = waived.notifications.get(kind=Notification.Kind.DUE_DAY_REMINDER)
        self.assertEqual(reminder.suppression_reason, "assignment_waived")
        replacement = create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                                         employee_ids=[self.employee.id], due_date_value=self.due, now=self.now)
        self.assertEqual(replacement[0].outcome, "created")
        self.assertEqual(self.assignment.slide_progress.count(), 0)

    def test_waiver_requires_reason_and_another_administrator_for_own_work(self):
        with self.assertRaises(AssignmentInputError):
            waive_assignment(assignment_id=self.assignment.id, actor=self.admin, reason=" ")
        own = create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                                 employee_ids=[self.admin.id], due_date_value=self.due, now=self.now)[0].assignment
        with self.assertRaisesMessage(AssignmentAccessError, "Another administrator must waive your own assignment."):
            waive_assignment(assignment_id=own.id, actor=self.admin, reason="Mine")

    def test_learner_can_review_waived_work_but_cannot_acknowledge(self):
        waive_assignment(assignment_id=self.assignment.id, actor=self.admin, reason="Waived", now=self.now)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        self.assertContains(self.client.get(reverse("my_training")), "<h2>Waived</h2>", html=True)
        self.assertContains(self.client.get(reverse("assignment_detail", args=[self.assignment.id])),
                            "Assignment waived")
        self.assertEqual(self.client.get(reverse("assignment_slide", args=[self.assignment.id, self.slide.id])).status_code,
                         200)
        self.assertEqual(self.client.post(reverse("acknowledge", args=[self.assignment.id, self.slide.id])).status_code,
                         403)

    def test_my_training_shows_waived_section_only_for_own_waived_assignments(self):
        self.assertNotContains(self.client.get(reverse("my_training")), "<h2>Waived</h2>", html=True)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        self.assertNotContains(self.client.get(reverse("my_training")), "<h2>Waived</h2>", html=True)

        waive_assignment(assignment_id=self.assignment.id, actor=self.admin, reason="Waived", now=self.now)
        other = Employee.objects.create(name="Bob", email="bob@example.test", is_demo=True)
        self.client.post(reverse("demo_switch"), {"employee_id": str(other.id)})
        self.assertNotContains(self.client.get(reverse("my_training")), "<h2>Waived</h2>", html=True)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        self.assertContains(self.client.get(reverse("my_training")), "<h2>Waived</h2>", html=True)

    def test_dashboard_export_and_constraint_treat_waiver_as_its_own_state(self):
        waive_assignment(assignment_id=self.assignment.id, actor=self.admin, reason="Waived", now=self.now)
        self.assertFalse(report_assignments(ReportFilters(), now=self.now).exists())
        rows = report_rows(report_assignments(ReportFilters(state="waived"), now=self.now), now=self.now)
        self.assertEqual((rows[0].state, rows[0].deadline), ("Waived", "—"))
        exported = list(export_rows(ReportFilters(state="waived"), now=self.now, zone_name="UTC"))
        self.assertEqual(exported[1][8], "Waived")
        self.assignment.refresh_from_db()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assignment.objects.filter(pk=self.assignment.id).update(completed_at=self.now)
