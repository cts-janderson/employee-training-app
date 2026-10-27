import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Assignment, Employee, Notification, Question, QuestionOption, Quiz, QuizAttempt, Training, TrainingVersion,
)
from .notifications import process_ready_notifications


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test",
                   ORG_TIME_ZONE="America/New_York", APP_BASE_URL="http://127.0.0.1:8000")
class IntegratedHciJourneyTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(
            name="Demo Administrator", email="demo.admin@example.test",
            verified_email="demo.admin@example.test", role=Employee.Role.ADMINISTRATOR, is_demo=True,
        )
        self.learner = Employee.objects.create(
            name="Learner", email="learner@example.test", verified_email="learner@example.test", is_demo=True,
        )
        self.overdue_employee = Employee.objects.create(
            name="Overdue Learner", email="overdue@example.test", verified_email="overdue@example.test", is_demo=True,
        )
        self.inactive = Employee.objects.create(
            name="Inactive Learner", email="inactive@example.test", is_demo=True, is_active=False,
        )

    def test_author_to_learner_to_follow_up_journey(self):
        self.assertEqual(self.client.post(reverse("new_training")).status_code, 302)
        training = Training.objects.get()
        draft = training.versions.get()
        editor = reverse("draft_editor", args=[training.id])
        self.assertEqual(self.client.post(editor, {
            "revision": draft.revision, "title": "Safety", "description": "", "action": "add",
        }).status_code, 302)
        draft.refresh_from_db()
        slide = draft.slides.get()
        self.assertEqual(self.client.post(editor, {
            "revision": draft.revision, "title": "Safety", "description": "Read then answer",
            "slide_id": [str(slide.id)], "slide_header": ["Read this"],
            "slide_body": ["Report incidents"], "slide_footer": [""], "action": "save",
        }).status_code, 302)
        draft.refresh_from_db()
        quiz = Quiz.objects.create(version=draft, passing_percent=100)
        question = Question.objects.create(quiz=quiz, position=1, type=Question.Type.SINGLE_CHOICE,
                                           prompt="What should you do?")
        correct = QuestionOption.objects.create(question=question, position=1, text="Report it", is_correct=True)
        wrong = QuestionOption.objects.create(question=question, position=2, text="Ignore it")
        review_url = reverse("publication_review", args=[training.id])
        review = self.client.get(review_url)
        self.assertContains(review, "Read then answer")
        self.assertContains(review, "Report incidents")
        self.assertContains(review, "What should you do?")
        self.assertRedirects(self.client.post(review_url, {
            "version_id": str(draft.id), "revision": draft.revision,
        }), reverse("preview", args=[training.id, draft.id]))

        due_date = (timezone.localdate() + timedelta(days=1)).isoformat()
        assign_url = reverse("assign_training", args=[training.id])
        selection = {
            "operation_id": str(uuid.uuid4()), "version_id": str(draft.id), "due_date": due_date,
            "employee_id": [str(self.learner.id), str(self.overdue_employee.id), str(self.inactive.id)],
        }
        assignment_review = self.client.post(assign_url, {**selection, "action": "review"})
        self.assertContains(assignment_review, "3 recipients")
        self.assertContains(assignment_review, "Inactive — no assignment")
        assigned = self.client.post(assign_url, {**selection, "action": "submit"})
        self.assertContains(assigned, "2 created")
        self.assertContains(assigned, "1 inactive")
        learner_assignment = Assignment.objects.get(employee=self.learner)
        overdue_assignment = Assignment.objects.get(employee=self.overdue_employee)

        self.client.post(reverse("demo_switch"), {"employee_id": str(self.learner.id)})
        self.assertContains(self.client.get(reverse("my_training")), "Start")
        self.client.get(reverse("assignment_slide", args=[learner_assignment.id, slide.id]))
        self.assertContains(self.client.get(reverse("my_training")), "Resume")
        self.client.post(reverse("acknowledge", args=[learner_assignment.id, slide.id]))
        self.assertContains(self.client.get(reverse("assignment_detail", args=[learner_assignment.id])), "Take quiz")
        quiz_url = reverse("take_quiz", args=[learner_assignment.id])
        submit_url = reverse("submit_quiz", args=[learner_assignment.id])
        self.assertContains(self.client.post(submit_url, {"submission_id": str(uuid.uuid4())}),
                            "Choose one answer", status_code=400)
        self.assertEqual(self.client.post(submit_url, {
            "submission_id": str(uuid.uuid4()), f"answer_{question.id}": str(wrong.id),
        }).status_code, 302)
        self.assertEqual(QuizAttempt.objects.filter(assignment=learner_assignment, passed=False).count(), 1)
        self.assertEqual(self.client.post(submit_url, {
            "submission_id": str(uuid.uuid4()), f"answer_{question.id}": str(correct.id),
        }).status_code, 302)
        self.assertContains(self.client.get(reverse("assignment_detail", args=[learner_assignment.id])),
                            "Training complete")
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.admin.id)})

        later = overdue_assignment.due_at + timedelta(hours=1)
        with patch("training.assignment_admin_views.timezone.now", return_value=later):
            dashboard = self.client.get(reverse("assignment_dashboard"), {"deadline": "overdue"})
        self.assertContains(dashboard, "Overdue Learner")
        self.assertEqual([row["assignment"].id for row in dashboard.context["rows"]], [overdue_assignment.id])
        queued_response = self.client.post(reverse("send_manual_nudge", args=[overdue_assignment.id]))
        self.assertEqual(queued_response.status_code, 302)
        nudge = Notification.objects.get(assignment=overdue_assignment, kind=Notification.Kind.MANUAL_NUDGE)
        self.assertContains(self.client.get(queued_response.url), "Queued for delivery")
        process_ready_notifications(now=later)
        nudge.refresh_from_db()
        self.assertEqual(nudge.status, Notification.Status.SENT)
        self.assertContains(self.client.get(queued_response.url), "Sent to local sink")

        self.assertEqual(self.client.post(reverse("cancel_assignment", args=[overdue_assignment.id]), {
            "reason": "Updated material",
        }).status_code, 302)
        self.assertEqual(self.client.post(reverse("new_revision", args=[training.id])).status_code, 302)
        revised = training.versions.get(version_number=2)
        self.assertContains(self.client.get(review_url), "version 2")
        self.assertEqual(self.client.post(review_url, {
            "version_id": str(revised.id), "revision": revised.revision,
        }).status_code, 302)
        replacement = self.client.post(assign_url, {
            "operation_id": str(uuid.uuid4()), "version_id": str(revised.id),
            "due_date": (timezone.localdate() + timedelta(days=2)).isoformat(),
            "employee_id": [str(self.overdue_employee.id)], "action": "submit",
        })
        self.assertContains(replacement, "1 created")
        self.assertEqual(Assignment.objects.get(pk=overdue_assignment.id).version_id, draft.id)
        self.assertEqual(Assignment.objects.filter(employee=self.overdue_employee).count(), 2)
