"""Saved review must never certify unsaved or conflicting authoring input."""
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Employee, Question, QuestionOption, Quiz, Slide, Training, TrainingVersion
from .services import attach_library_quiz, publish_saved_draft


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="admin@example.test")
class QuizWorkspaceTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.admin = Employee.objects.create(name="Admin", email="admin@example.test",
            role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.version = TrainingVersion.objects.create(training=Training.objects.create(),
            version_number=1, title="Safety")
        Slide.objects.create(version=self.version, position=1, header="Read", body="Read carefully")
        self.quiz = Quiz.objects.create(title="Safety check", passing_percent=75)
        self.question = Question.objects.create(quiz=self.quiz, position=1,
            type=Question.Type.SINGLE_CHOICE, prompt="Report an incident?")
        self.yes = QuestionOption.objects.create(question=self.question, position=1, text="Yes", is_correct=True)
        self.no = QuestionOption.objects.create(question=self.question, position=2, text="No")
        self.url = reverse("library_quiz_editor", args=[self.quiz.pk])

    def payload(self, **changes):
        return {"revision": self.quiz.revision, "title": self.quiz.title,
            "passing_percent": "75", "action": "save", "question_id": [str(self.question.pk)],
            "question_type": [self.question.type], "question_prompt": [self.question.prompt],
            f"option_id_{self.question.pk}": [str(self.yes.pk), str(self.no.pk)],
            f"option_text_{self.question.pk}": ["Yes", "No"],
            f"correct_{self.question.pk}": str(self.yes.pk), **changes}

    def test_ready_library_review_links_to_copy_review_and_saved_preview(self):
        response = self.client.get(self.url)
        for fragment in ("#quiz-setup", "#quiz-questions", "#quiz-review"):
            self.assertContains(response, f'href="{fragment}"')
        self.assertContains(response, "Ready for attachment")
        self.assertContains(response, reverse("attach_library_quiz", args=[self.version.training_id]) + f"?quiz={self.quiz.pk}")
        self.assertContains(response, reverse("quiz_learner_preview", args=[self.quiz.pk]))
        self.assertContains(response, "Preview saved quiz as learner")

    def test_readiness_issues_link_to_saved_fields_without_marking_them_invalid(self):
        self.quiz.title = ""
        self.quiz.passing_percent = None
        self.quiz.save()
        self.question.prompt = ""
        self.question.save()
        response = self.client.get(self.url)
        self.assertContains(response, 'href="#quiz-title"')
        self.assertContains(response, 'href="#passing-percent"')
        self.assertContains(response, f'href="#prompt-{self.question.pk}"')
        self.assertNotContains(response, 'aria-invalid="true"')
        self.assertNotContains(response, "Attach a saved copy")

    def test_invalid_edits_leave_saved_readiness_and_content_unchanged(self):
        response = self.client.post(self.url, self.payload(title="Unsaved title", passing_percent="bad"))
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.context["saved_quiz_ready"])
        self.assertContains(response, "Your current edits are unsaved", status_code=400)
        self.assertContains(response, 'value="Unsaved title"', status_code=400)
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.title, "Safety check")

    def test_conflict_preserves_submission_but_withholds_next_actions(self):
        response = self.client.post(self.url, self.payload(revision=99, title="My edits"))
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'value="My edits"', status_code=409)
        self.assertNotContains(response, "Attach a saved copy", status_code=409)

    def test_removal_review_and_cancel_preserve_unsaved_inputs(self):
        response = self.client.post(self.url, self.payload(title="Edited", action=f"review_remove_option:{self.no.pk}"))
        self.assertContains(response, "Confirm removal")
        self.assertContains(response, 'value="Edited"')
        response = self.client.post(self.url, self.payload(title="Edited", action="cancel_remove"))
        self.assertContains(response, 'value="Edited"')
        self.assertTrue(QuestionOption.objects.filter(pk=self.no.pk).exists())
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.title, "Safety check")

    def test_incomplete_save_updates_readiness_and_ready_copy_can_publish(self):
        self.assertEqual(self.client.post(self.url, self.payload(question_prompt=[""])).status_code, 302)
        response = self.client.get(self.url)
        self.assertContains(response, "Question 1: add a prompt")
        self.assertNotContains(response, "Attach a saved copy")
        self.quiz.refresh_from_db()
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        self.quiz.refresh_from_db()
        attach_library_quiz(version_id=self.version.pk, quiz_id=self.quiz.pk,
            expected_version_revision=0, expected_quiz_revision=self.quiz.revision, confirm="attach")
        attached_url = reverse("quiz_editor", args=[self.version.training_id])
        response = self.client.get(attached_url)
        self.assertContains(response, "Ready for publication review")
        self.assertContains(response, reverse("publication_review", args=[self.version.training_id]))
        self.assertNotContains(response, "Attach a saved copy")
        self.version.refresh_from_db()
        publish_saved_draft(version_id=self.version.pk, expected_revision=self.version.revision)
        self.assertEqual(self.version.training.versions.get().status, "published")

    def test_learner_cannot_access_either_workspace(self):
        self.admin.role = Employee.Role.EMPLOYEE
        self.admin.save()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(reverse("quiz_editor", args=[self.version.training_id])).status_code, 403)


    def test_quiz_responses_keep_the_dynamic_workspace_on_saved_error_and_review_pages(self):
        url = self.url
        responses = [self.client.get(url),
            self.client.post(url, self.payload(passing_percent="invalid")),
            self.client.post(url, self.payload(revision=99)),
            self.client.post(url, self.payload(action=f"review_remove_option:{self.no.pk}"))]
        self.assertEqual([response.status_code for response in responses], [200, 400, 409, 200])
        for response in responses:
            with self.subTest(status=response.status_code):
                self.assertContains(response, 'id="quiz-editor-workspace"', status_code=response.status_code)
                self.assertContains(response, f'data-question-id="{self.question.pk}"', status_code=response.status_code)
                self.assertContains(response, 'id="quiz-request-status"', status_code=response.status_code)
        attached = self.client.get(reverse("quiz_editor", args=[self.version.training_id]))
        self.assertContains(attached, 'id="quiz-editor-workspace"')
        self.assertContains(attached, 'value="add_quiz"')
