from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Assignment, Employee, Quiz, Training, TrainingVersion, UNTITLED_TRAINING


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="titles.admin@example.test")
class TrainingDisplayTitleTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(name="Admin", email="titles.admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)

    def version(self, training, number, title, status):
        return TrainingVersion.objects.create(training=training, version_number=number,
                                              title=title, status=status)

    def test_annotation_prefers_newest_published_then_draft_then_fallback(self):
        cases = []
        first = Training.objects.create(is_demo=True)
        self.version(first, 1, "Published one", TrainingVersion.Status.PUBLISHED)
        self.version(first, 2, "Draft two", TrainingVersion.Status.DRAFT)
        cases.append((first, "Published one"))
        second = Training.objects.create(is_demo=True)
        self.version(second, 1, "Published one", TrainingVersion.Status.PUBLISHED)
        self.version(second, 2, "Published two", TrainingVersion.Status.PUBLISHED)
        cases.append((second, "Published two"))
        third = Training.objects.create(is_demo=True)
        self.version(third, 1, "Draft only", TrainingVersion.Status.DRAFT)
        cases.append((third, "Draft only"))
        fourth = Training.objects.create(is_demo=True)
        self.version(fourth, 1, "", TrainingVersion.Status.DRAFT)
        cases.append((fourth, UNTITLED_TRAINING))
        fifth = Training.objects.create(is_demo=True)
        self.version(fifth, 1, "Published", TrainingVersion.Status.PUBLISHED)
        self.version(fifth, 2, "", TrainingVersion.Status.DRAFT)
        cases.append((fifth, "Published"))
        for training, expected in cases:
            with self.subTest(training=training.id):
                self.assertEqual(Training.objects.with_display_title().get(pk=training.pk).display_title, expected)

    def test_training_list_uses_annotation_and_case_insensitive_order(self):
        for title in ("Zeta", "", "alpha"):
            training = Training.objects.create(is_demo=True)
            self.version(training, 1, title, TrainingVersion.Status.DRAFT)
        response = self.client.get(reverse("training_list"))
        self.assertEqual(response.status_code, 200)
        rows = response.context["rows"]
        self.assertEqual([row["display_title"] for row in rows], ["alpha", UNTITLED_TRAINING, "Zeta"])
        for row in rows:
            self.assertEqual(row["display_title"],
                             Training.objects.with_display_title().get(pk=row["training"].pk).display_title)

    def test_dashboard_menu_and_both_charts_use_display_titles(self):
        now = timezone.now()
        for index, title in enumerate(("Zeta", "alpha"), 1):
            training = Training.objects.create(is_demo=True)
            version = self.version(training, 1, title, TrainingVersion.Status.DRAFT)
            Quiz.objects.create(version=version, passing_percent=80)
            version.status = TrainingVersion.Status.PUBLISHED
            version.save()
            employee = Employee.objects.create(name=f"Learner {index}",
                                               email=f"learner{index}@example.test", is_demo=True)
            Assignment.objects.create(
                employee=employee, training=training, version=version,
                due_date=(now + timedelta(days=1)).date(), due_timezone="UTC",
                due_at=now + timedelta(days=1),
            )
        pending = self.client.get(reverse("assignment_dashboard"))
        self.assertEqual([row["label"] for row in pending.context["trainings"]], ["alpha", "Zeta"])
        self.assertEqual({row["name"] for row in pending.context["chart"]["rows"]}, {"alpha", "Zeta"})
        quiz = self.client.get(reverse("assignment_dashboard"), {"mode": "quiz"})
        self.assertEqual([row["label"] for row in quiz.context["trainings"]], ["alpha", "Zeta"])
        self.assertEqual({row["name"] for row in quiz.context["quiz_chart"]["all_rows"]}, {"alpha", "Zeta"})
