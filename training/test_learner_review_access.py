import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignments import (
    AssignmentAccessError, LearnerAccessLevel, LearnerDenial, acknowledge_slide, learner_access,
)
from .models import (
    Assignment, AssignmentSlideProgress, Employee, Question, QuestionOption, Quiz, QuizAttempt,
    Slide, Training, TrainingVersion,
)


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="review.admin@example.test")
class LearnerReviewAccessTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.now = timezone.now()
        self.admin = Employee.objects.create(name="Admin", email="review.admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.owner = Employee.objects.create(name="Owner", email="review.owner@example.test", is_demo=True)
        self.other = Employee.objects.create(name="Other", email="review.other@example.test", is_demo=True)
        self.training = Training.objects.create(is_demo=True)
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Review")
        self.slide = Slide.objects.create(version=self.version, position=1, header="Read", body="Read")
        self.quiz = Quiz.objects.create(version=self.version, passing_percent=80)
        self.question = Question.objects.create(quiz=self.quiz, position=1, type=Question.Type.SINGLE_CHOICE,
                                                prompt="Choose")
        self.correct = QuestionOption.objects.create(question=self.question, position=1, text="Yes", is_correct=True)
        QuestionOption.objects.create(question=self.question, position=2, text="No", is_correct=False)
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.save()

    def assignment(self, ending=None, *, archived=False, employee=None):
        assignment = Assignment.objects.create(
            employee=employee or self.owner, training=self.training, version=self.version,
            due_date=(self.now + timedelta(days=1)).date(), due_timezone="UTC",
            due_at=self.now + timedelta(days=1),
        )
        AssignmentSlideProgress.objects.create(assignment=assignment, slide=self.slide, acknowledged_at=self.now)
        changes = {}
        if ending == "completed":
            changes["completed_at"] = self.now
        elif ending == "canceled":
            changes["canceled_at"] = self.now
        elif ending == "waived":
            changes["waived_at"] = self.now
        if archived:
            changes["archived_at"] = self.now
        if changes:
            Assignment.objects.filter(pk=assignment.pk).update(**changes)
            assignment.refresh_from_db()
        return assignment

    def test_level_and_message_by_ending_caller_activity_and_archiving(self):
        expected = {
            None: (None, LearnerAccessLevel.ACT, None),
            "completed": (None, LearnerAccessLevel.ACT, None),
            "canceled": (LearnerDenial.CANCELED, LearnerAccessLevel.NONE, "This assignment was canceled."),
            "waived": (LearnerDenial.WAIVED, LearnerAccessLevel.REVIEW, "This assignment was waived."),
        }
        for ending, (denial, level, message) in expected.items():
            for archived in (False, True):
                with self.subTest(ending=ending, archived=archived):
                    self.owner = Employee.objects.create(
                        name="Owner", email=f"owner-{uuid.uuid4()}@example.test", is_demo=True,
                    )
                    assignment = self.assignment(ending, archived=archived)
                    access = learner_access(assignment.id, self.owner)
                    self.assertEqual((access.denial, access.level, access.message), (denial, level, message))
                    self.assertEqual((access.can_review, access.can_act),
                                     (level != LearnerAccessLevel.NONE, level == LearnerAccessLevel.ACT))
                    self.assertEqual(access, (assignment, denial))
                    self.assertEqual(access[1], denial)
                    foreign = learner_access(assignment.id, self.other)
                    self.assertEqual((foreign.denial, foreign.level, foreign.message),
                                     (LearnerDenial.NOT_YOURS, LearnerAccessLevel.NONE,
                                      "This assignment is not available to you."))
                    self.owner.is_active = False
                    self.owner.save(update_fields=["is_active"])
                    inactive = learner_access(assignment.id, self.owner)
                    self.assertEqual((inactive.denial, inactive.level, inactive.message),
                                     (LearnerDenial.INACTIVE, LearnerAccessLevel.NONE,
                                      "This assignment is not available to you."))
                    self.owner.is_active = True
                    self.owner.save(update_fields=["is_active"])
        for assignment_id in (uuid.uuid4(), "not-a-uuid"):
            missing = learner_access(assignment_id, self.owner)
            self.assertEqual((missing.denial, missing.level, missing.message),
                             (LearnerDenial.MISSING, LearnerAccessLevel.NONE, None))

    def test_owned_assignment_raises_the_access_message(self):
        assignment = self.assignment("waived")
        with self.assertRaisesMessage(AssignmentAccessError, "This assignment was waived."):
            acknowledge_slide(assignment_id=assignment.id, slide_id=self.slide.id, employee=self.owner)
        with self.assertRaises(Assignment.DoesNotExist):
            acknowledge_slide(assignment_id=uuid.uuid4(), slide_id=self.slide.id, employee=self.owner)

    def test_learner_response_matrix_for_pending_completed_canceled_and_waived(self):
        cases = ((None, (200, 200, 302, 200, 400, 200)),
                 ("completed", (200, 200, 302, 302, 400, 200)),
                 ("canceled", (404, 404, 404, 409, 403, 404)),
                 ("waived", (200, 200, 403, 409, 403, 200)))
        for ending, expected in cases:
            with self.subTest(ending=ending):
                self.owner = Employee.objects.create(
                    name="Owner", email=f"owner-{uuid.uuid4()}@example.test", is_demo=True,
                )
                self.client.post(reverse("demo_switch"), {"employee_id": str(self.owner.id)})
                assignment = self.assignment(ending)
                attempt = QuizAttempt.objects.create(
                    assignment=assignment, quiz=self.quiz, submission_id=uuid.uuid4(),
                    request_fingerprint="x" * 64, correct_count=0, question_count=1,
                    passed=False, submitted_at=self.now,
                )
                paths = (
                    reverse("assignment_detail", args=[assignment.id]),
                    reverse("assignment_slide", args=[assignment.id, self.slide.id]),
                    reverse("acknowledge", args=[assignment.id, self.slide.id]),
                    reverse("take_quiz", args=[assignment.id]),
                    reverse("submit_quiz", args=[assignment.id]),
                    reverse("quiz_result", args=[assignment.id, attempt.id]),
                )
                responses = [self.client.post(path, {"submission_id": str(uuid.uuid4())})
                             if index in (2, 4) else self.client.get(path)
                             for index, path in enumerate(paths)]
                self.assertEqual(tuple(response.status_code for response in responses), expected)
                if ending == "waived":
                    self.assertContains(responses[2], "This assignment was waived.", status_code=403)
                    self.assertContains(responses[4], "This assignment was waived.", status_code=403)
                if ending == "canceled":
                    self.assertContains(responses[4], "before your quiz was submitted", status_code=403)

    def test_waiver_races_preserve_forbidden_responses(self):
        assignment = self.assignment()
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.owner.id)})

        def waive_and_refuse(*args, **kwargs):
            Assignment.objects.filter(pk=assignment.pk).update(waived_at=self.now)
            raise AssignmentAccessError("This assignment was waived.")

        with patch("training.assignment_views.acknowledge_slide", side_effect=waive_and_refuse):
            response = self.client.post(reverse("acknowledge", args=[assignment.id, self.slide.id]))
        self.assertContains(response, "This assignment was waived.", status_code=403)
        Assignment.objects.filter(pk=assignment.pk).update(waived_at=None)
        payload = {"submission_id": str(uuid.uuid4()), f"answer_{self.question.id}": str(self.correct.id)}
        with patch("training.quiz_learner_views.submit_quiz", side_effect=waive_and_refuse):
            response = self.client.post(reverse("submit_quiz", args=[assignment.id]), payload)
        self.assertContains(response, "This assignment was waived.", status_code=403)
        self.assertFalse(QuizAttempt.objects.filter(assignment=assignment).exists())
