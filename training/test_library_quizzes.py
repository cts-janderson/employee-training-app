import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignments import acknowledge_slide, create_assignments
from .models import Assignment, Employee, Question, QuestionOption, Quiz, Slide, Training, TrainingVersion
from .quizzes import submit_quiz
from .services import publish_saved_draft


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
class LibraryQuizTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(
            name="Administrator", email="demo.admin@example.test",
            role=Employee.Role.ADMINISTRATOR, is_demo=True,
        )
        self.source = Quiz.objects.create(title="Safety check", passing_percent=80)
        self.question = Question.objects.create(
            quiz=self.source, position=1, type=Question.Type.SINGLE_CHOICE, prompt="What is safe?",
        )
        self.correct = QuestionOption.objects.create(
            question=self.question, position=1, text="Report it", is_correct=True,
        )
        QuestionOption.objects.create(question=self.question, position=2, text="Ignore it")

    def draft(self):
        training = Training.objects.create()
        version = TrainingVersion.objects.create(training=training, version_number=1, title="Safety")
        Slide.objects.create(version=version, position=1, header="Read", body="Report incidents")
        return version

    def attach(self, version, *, confirm="attach", source_revision=None, version_revision=None):
        return self.client.post(reverse("attach_library_quiz", args=[version.training_id]), {
            "version_id": str(version.id), "version_revision": version.revision if version_revision is None else version_revision,
            "quiz_id": str(self.source.id), "quiz_revision": self.source.revision if source_revision is None else source_revision,
            "confirm": confirm,
        })

    def test_library_editor_saves_incomplete_work_then_ready_quiz(self):
        created = self.client.post(reverse("new_library_quiz"))
        self.assertEqual(created.status_code, 302)
        quiz = Quiz.objects.filter(version__isnull=True).exclude(pk=self.source.id).get()
        url = reverse("library_quiz_editor", args=[quiz.id])
        self.assertContains(self.client.get(url), "Edit library quiz")
        self.assertEqual(self.client.post(url, {
            "revision": 0, "title": "Incident check", "passing_percent": "75", "action": "add_single",
        }).status_code, 302)
        quiz.refresh_from_db()
        self.assertEqual((quiz.title, quiz.revision), ("Incident check", 1))
        question = quiz.questions.get()
        options = list(question.options.all())
        self.assertContains(self.client.get(reverse("library_quiz_list")), "Needs work before attachment")
        self.assertEqual(self.client.post(url, {
            "revision": quiz.revision, "title": "Incident check", "passing_percent": "75", "action": "save",
            "question_id": [str(question.id)], "question_type": [question.type],
            "question_prompt": ["Report an incident?"],
            f"option_id_{question.id}": [str(option.id) for option in options],
            f"option_text_{question.id}": ["Yes", "No"],
            f"correct_{question.id}": str(options[0].id),
        }).status_code, 302)
        quiz.refresh_from_db()
        self.assertContains(self.client.get(reverse("library_quiz_list")), "Ready to attach")
        self.assertEqual(quiz.questions.get().prompt, "Report an incident?")
        stale = self.client.post(url, {"revision": 1, "title": "Stale", "passing_percent": "75", "action": "save"})
        self.assertContains(stale, "A newer library quiz was saved", status_code=409)

    def test_library_editor_stale_invalid_submission_and_removal_review_return_conflict(self):
        url = reverse("library_quiz_editor", args=[self.source.id])
        invalid = {"revision": self.source.revision + 1, "title": "Safety check",
                   "passing_percent": "bad", "action": "save", "question_id": [str(self.question.id)],
                   "question_type": [], "question_prompt": []}
        self.assertEqual(self.client.post(url, invalid).status_code, 409)
        review = {**invalid, "action": f"review_remove_option:{self.correct.id}"}
        self.assertEqual(self.client.post(url, review).status_code, 409)
        current = {**review, "revision": self.source.revision}
        response = self.client.post(url, current)
        self.assertContains(response, "Confirm removal")

    def test_attach_copies_to_multiple_drafts_and_source_edits_do_not_propagate(self):
        first = self.draft()
        second = self.draft()
        chooser = reverse("attach_library_quiz", args=[first.training_id])
        self.assertContains(self.client.get(reverse("library_quiz_list")),
                            chooser + f"?quiz={self.source.id}")
        review = self.client.get(chooser, {"quiz": str(self.source.id)})
        self.assertContains(review, "What is safe?")
        self.assertContains(review, "Attach a copy of this quiz")
        self.assertEqual(self.attach(first).status_code, 302)
        self.assertEqual(self.attach(second).status_code, 302)
        first.refresh_from_db()
        second.refresh_from_db()
        first_quiz, second_quiz = first.quiz, second.quiz
        self.assertEqual((first.revision, second.revision), (1, 1))
        self.assertNotEqual(first_quiz.id, second_quiz.id)
        self.assertNotEqual(first_quiz.questions.get().id, self.question.id)
        self.assertNotEqual(first_quiz.questions.get().options.first().id, self.correct.id)
        self.assertIsNone(self.source.version_id)
        self.question.prompt = "Changed source prompt"
        self.question.save()
        self.assertEqual(first_quiz.questions.get().prompt, "What is safe?")
        self.assertEqual(second_quiz.questions.get().prompt, "What is safe?")
        first_quiz.questions.update(prompt="Edited attached copy")
        self.assertEqual(self.source.questions.get().prompt, "Changed source prompt")

    def test_replacement_requires_fresh_revisions_and_keeps_old_quiz_on_conflict(self):
        version = self.draft()
        old = Quiz.objects.create(version=version, passing_percent=50)
        Question.objects.create(quiz=old, position=1, type=Question.Type.SINGLE_CHOICE, prompt="Old quiz")
        chooser = reverse("attach_library_quiz", args=[version.training_id])
        self.assertContains(self.client.get(chooser, {"quiz": str(self.source.id)}), "Current draft quiz will be replaced")
        old_source_revision = self.source.revision
        self.source.revision += 1
        self.source.save(update_fields=["revision"])
        stale = self.attach(version, confirm="replace", source_revision=old_source_revision)
        self.assertContains(stale, "draft or library quiz changed", status_code=409)
        self.assertEqual(Quiz.objects.get(version=version).id, old.id)
        replaced = self.attach(version, confirm="replace")
        self.assertEqual(replaced.status_code, 302)
        self.assertFalse(Quiz.objects.filter(pk=old.id).exists())
        self.assertEqual(Quiz.objects.get(version=version).questions.get().prompt, "What is safe?")
        version.refresh_from_db()
        self.assertEqual(version.revision, 1)
        self.assertContains(self.attach(version, confirm="replace", version_revision=0),
                            "draft or library quiz changed", status_code=409)

    def test_unready_quiz_and_employee_role_cannot_attach(self):
        version = self.draft()
        self.source.title = ""
        self.source.save(update_fields=["title"])
        review = self.client.get(reverse("attach_library_quiz", args=[version.training_id]),
                                 {"quiz": str(self.source.id)})
        self.assertContains(review, "Needs work before attachment")
        self.assertNotContains(review, 'name="confirm"')
        self.assertEqual(self.attach(version).status_code, 409)
        self.assertFalse(Quiz.objects.filter(version=version).exists())
        self.admin.role = Employee.Role.EMPLOYEE
        self.admin.save()
        self.assertEqual(self.client.get(reverse("library_quiz_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("library_quiz_editor", args=[self.source.id])).status_code, 403)
        self.assertEqual(self.client.get(reverse("attach_library_quiz", args=[version.training_id])).status_code, 403)

    def test_attached_copy_grades_and_published_content_cannot_move_to_library(self):
        learner = Employee.objects.create(name="Learner", email="learner@example.test", is_demo=True)
        version = self.draft()
        self.attach(version)
        version.refresh_from_db()
        publish_saved_draft(version_id=version.id, expected_revision=version.revision)
        attached = Quiz.objects.get(version=version)
        question = attached.questions.get()
        answer = question.options.get(is_correct=True)
        with self.assertRaises(ValidationError):
            attached.version = None
            attached.save()
        with self.assertRaises(ValidationError):
            question.quiz = self.source
            question.save()
        with self.assertRaises(ValidationError):
            answer.question = self.question
            answer.save()
        due_date = (timezone.localdate() + timedelta(days=1)).isoformat()
        create_assignments(
            operation_id=uuid.uuid4(), actor=self.admin, version_id=version.id,
            employee_ids=[learner.id], due_date_value=due_date,
        )
        assignment = Assignment.objects.get(employee=learner)
        acknowledge_slide(assignment_id=assignment.id, slide_id=version.slides.get().id, employee=learner)
        attempt = submit_quiz(
            assignment_id=assignment.id, employee=learner,
            submission_id=uuid.uuid4(), answers={question.id: answer.id},
        )
        self.assertTrue(attempt.passed)
        self.assertEqual(attempt.quiz_id, attached.id)

    def test_library_editor_changes_selection_type_and_previews_saved_content(self):
        url = reverse("library_quiz_editor", args=[self.source.id])
        options = list(self.question.options.all())
        payload = {
            "revision": self.source.revision, "title": self.source.title, "passing_percent": "80",
            "action": f"change_type:{self.question.id}:multiple_selection",
            "question_id": [str(self.question.id)], "question_type": [self.question.type],
            "question_prompt": [self.question.prompt],
            f"option_id_{self.question.id}": [str(option.id) for option in options],
            f"option_text_{self.question.id}": [option.text for option in options],
            f"correct_{self.question.id}": str(self.correct.id),
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith(f"#question-heading-{self.question.id}"))
        self.question.refresh_from_db()
        self.assertEqual(self.question.type, Question.Type.MULTIPLE_SELECTION)
        self.assertEqual(list(self.question.options.values_list("id", flat=True)), [option.id for option in options])
        editor = self.client.get(url)
        self.assertContains(editor, "Preview saved quiz as learner")
        preview = self.client.get(reverse("quiz_learner_preview", args=[self.source.id]))
        self.assertContains(preview, "Learner preview")
        self.assertContains(preview, "What is safe?")
        self.assertNotContains(preview, "Report it — correct")
