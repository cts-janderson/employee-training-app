import uuid
from datetime import timedelta

from django.template.loader import get_template
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .question_types import for_display, for_type
from .models import (
    Assignment, AssignmentSlideProgress, Employee, Question, QuestionOption, Quiz, Slide,
    Training, TrainingVersion,
)


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="presentation.admin@example.test")
class QuestionPresentationTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(name="Admin", email="presentation.admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.learner = Employee.objects.create(name="Learner", email="presentation.learner@example.test",
                                               is_demo=True)
        self.training = Training.objects.create()
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        self.slide = Slide.objects.create(version=self.version, position=1, header="Read", body="Material")
        self.quiz = Quiz.objects.create(version=self.version, passing_percent=80)
        self.questions = self.populate(self.quiz)

    def populate(self, quiz):
        rows = []
        for position, kind in enumerate(Question.Type.values, 1):
            question = Question.objects.create(quiz=quiz, position=position, type=kind,
                                               prompt=f"Question {position} prompt")
            texts = ("True", "False") if kind == Question.Type.TRUE_FALSE else ("First", "Second")
            for index, value in enumerate(texts, 1):
                QuestionOption.objects.create(
                    question=question, position=index, text=value,
                    is_correct=(index == 1 and kind != Question.Type.ORDERING),
                    required_rank=index if kind == Question.Type.ORDERING else None,
                )
            rows.append(question)
        return rows

    def assert_editor_controls(self, response):
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertEqual(content.count("Options — select the correct answer"), 2)
        self.assertEqual(content.count("Options — select every correct answer"), 1)
        self.assertEqual(content.count("Options — enter the required sequence"), 1)
        single, true_false, multiple, ordering = self.questions
        for question in (single, true_false):
            self.assertIn(f'name="correct_{question.id}"', content)
        self.assertIn(f'name="correct_set_{multiple.id}"', content)
        self.assertIn(f'name="rank_{ordering.id}"', content)
        self.assertIn(f'id="rank-{ordering.options.first().id}"', content)
        self.assertIn("Required rank for option 1", content)
        self.assertIn("Option 1 is correct", content)
        self.assertIn("Option 1 text (required to publish)", content)
        self.assertIn(f'name="option_text_{true_false.id}" value="True"', content)
        self.assertNotIn(f'value="add_option:{true_false.id}"', content)
        for question in (single, multiple, ordering):
            self.assertIn(f'value="add_option:{question.id}"', content)
        self.assertNotIn("style=", content)

    def test_attached_quiz_editor_uses_each_type_control(self):
        self.assert_editor_controls(self.client.get(reverse("quiz_editor", args=[self.training.id])))

    def test_library_quiz_editor_uses_each_type_control(self):
        library = Quiz.objects.create(title="Library", passing_percent=80)
        self.questions = self.populate(library)
        self.assert_editor_controls(self.client.get(reverse("library_quiz_editor", args=[library.id])))

    def test_unknown_submitted_type_keeps_single_choice_fallback(self):
        question = self.questions[0]
        options = list(question.options.all())
        response = self.client.post(reverse("quiz_editor", args=[self.training.id]), {
            "revision": self.version.revision, "action": "save", "passing_percent": "80",
            "question_id": [str(question.id)], "question_type": ["bogus"],
            "question_prompt": [question.prompt],
            f"option_id_{question.id}": [str(option.id) for option in options],
            f"option_text_{question.id}": [option.text for option in options],
            f"correct_{question.id}": str(options[0].id),
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Options — select the correct answer", status_code=400)
        self.assertContains(response, f'name="correct_{question.id}"', status_code=400)

    def test_conflict_summary_keeps_rank_and_correct_answer_labels(self):
        question = self.questions[0]
        response = self.client.post(reverse("quiz_editor", args=[self.training.id]), {
            "revision": -1, "action": "save", "passing_percent": "80",
            "question_id": [str(question.id)], "question_type": [question.type],
            "question_prompt": ["My unsaved prompt"],
            f"option_id_{question.id}": [str(option.id) for option in question.options.all()],
            f"option_text_{question.id}": [option.text for option in question.options.all()],
        })
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "· rank 1", status_code=409)
        self.assertContains(response, "· selected as correct", status_code=409)

    def test_take_quiz_uses_each_type_control_and_error_state(self):
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.save()
        now = timezone.now()
        assignment = Assignment.objects.create(
            employee=self.learner, training=self.training, version=self.version,
            due_date=(now + timedelta(days=1)).date(), due_timezone="UTC",
            due_at=now + timedelta(days=1),
        )
        AssignmentSlideProgress.objects.create(assignment=assignment, slide=self.slide, acknowledged_at=now)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.learner.id)})
        response = self.client.get(reverse("take_quiz", args=[assignment.id]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(f'name="answer_{self.questions[0].id}"', content)
        self.assertIn(f'name="multi_{self.questions[2].id}"', content)
        self.assertIn(f'name="order_{self.questions[3].id}"', content)
        self.assertIn("Select every answer that applies.", content)
        self.assertIn("Choose each option once, in the order it should appear.", content)
        self.assertIn("Position 1 for question 4", content)
        self.assertEqual(content.count(" data-ordering\n"), 1)
        invalid = self.client.post(reverse("submit_quiz", args=[assignment.id]), {
            "submission_id": str(uuid.uuid4()),
        })
        self.assertEqual(invalid.status_code, 400)
        self.assertContains(invalid, 'aria-invalid="true"', status_code=400)

    def test_type_metadata_loads_and_unknown_display_falls_back(self):
        self.assertIs(for_display("bogus"), for_type(Question.Type.SINGLE_CHOICE))
        self.assertIs(for_display(""), for_type(Question.Type.SINGLE_CHOICE))
        for kind in Question.Type.values:
            with self.subTest(kind=kind):
                question_type = for_type(kind)
                get_template(question_type.editor_template)
                get_template(question_type.take_template)
                self.assertEqual(question_type.ranked, kind == Question.Type.ORDERING)
