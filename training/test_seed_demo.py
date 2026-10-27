from io import StringIO
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignments import deadline_status
from .models import (
    Assignment, Employee, EmployeeGroup, Notification, Question, Quiz, QuizAttempt,
    Training, TrainingVersion,
)
from .services import publication_problems
from .renewals import renewal_candidates


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test",
                   ORG_TIME_ZONE="America/New_York")
class SeedDemoTests(TestCase):
    def test_seed_creates_a_reviewable_dataset_and_rerun_changes_nothing(self):
        output = StringIO()
        call_command("seed_demo", stdout=output)

        self.assertEqual(Employee.objects.filter(is_demo=True).count(), 7)
        self.assertEqual(EmployeeGroup.objects.count(), 2)
        self.assertEqual(Training.objects.count(), 3)
        self.assertEqual(TrainingVersion.objects.filter(status="published").count(), 2)
        self.assertEqual(TrainingVersion.objects.filter(status="draft").count(), 1)
        self.assertEqual(Quiz.objects.filter(version__isnull=True).count(), 1)
        self.assertEqual(Assignment.objects.count(), 11)
        self.assertEqual(set(Question.objects.filter(quiz__version__title="Demo: Workplace Safety Essentials")
                             .values_list("type", flat=True)), set(Question.Type.values))
        for version in TrainingVersion.objects.filter(status="published"):
            self.assertEqual(publication_problems(version), [])

        safety = TrainingVersion.objects.get(title="Demo: Workplace Safety Essentials")
        handling = TrainingVersion.objects.get(title="Demo: Data Handling Basics")
        by_email = {employee.email: employee for employee in Employee.objects.all()}

        def assignment(version, key):
            return Assignment.objects.get(version=version, employee=by_email[f"demo.{key}@example.test"])

        self.assertIsNone(assignment(safety, "maya").started_at)
        self.assertEqual(assignment(safety, "leo").slide_progress.count(), 1)
        self.assertFalse(QuizAttempt.objects.get(assignment=assignment(safety, "priya")).passed)
        self.assertTrue(QuizAttempt.objects.get(assignment=assignment(safety, "omar")).passed)
        self.assertEqual(deadline_status(assignment(safety, "casey")), "overdue")
        self.assertFalse(by_email["demo.rowan@example.test"].is_active)
        self.assertEqual(deadline_status(assignment(handling, "maya")), "completed_late")
        self.assertIsNotNone(assignment(handling, "leo").completed_at)
        today = timezone.now().astimezone(ZoneInfo("America/New_York")).date()
        self.assertEqual([item.employee.id for item in renewal_candidates(handling.training, as_of=today)],
                         [by_email["demo.leo@example.test"].id])
        self.assertIsNotNone(assignment(handling, "priya").waived_at)
        self.assertIsNotNone(assignment(handling, "omar").canceled_at)
        self.assertIsNotNone(assignment(handling, "casey").archived_at)
        self.assertTrue(Notification.objects.filter(assignment=assignment(safety, "maya")).exists())

        self.client.defaults["HTTP_HOST"] = "localhost"
        self.assertContains(self.client.get(reverse("assignment_dashboard")), "Maya Chen")
        self.client.post(reverse("demo_switch"), {"employee_id": str(by_email["demo.priya@example.test"].id)})
        self.assertContains(self.client.get(reverse("my_training")), "Workplace Safety Essentials")

        counts = (Employee.objects.count(), Training.objects.count(), Assignment.objects.count(),
                  Notification.objects.count(), QuizAttempt.objects.count())
        call_command("seed_demo", stdout=output)
        self.assertIn("already exists", output.getvalue())
        self.assertEqual(counts, (Employee.objects.count(), Training.objects.count(),
                                  Assignment.objects.count(), Notification.objects.count(),
                                  QuizAttempt.objects.count()))

    def test_seed_requires_local_demo_mode_before_writing(self):
        with override_settings(DEBUG=False):
            with self.assertRaisesMessage(CommandError, "Demo seeds require"):
                call_command("seed_demo", stdout=StringIO())
        self.assertFalse(Employee.objects.exists())

    def test_seed_rolls_back_when_a_demo_email_is_already_real_data(self):
        Employee.objects.create(name="Existing person", email="demo.maya@example.test")
        with self.assertRaisesMessage(CommandError, "already exists"):
            call_command("seed_demo", stdout=StringIO())
        self.assertEqual(Employee.objects.count(), 1)
        self.assertFalse(Training.objects.exists())
