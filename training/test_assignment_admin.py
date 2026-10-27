import uuid
from datetime import timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignments import (
    AssignmentAccessError, AssignmentConflict, AssignmentInputError,
    acknowledge_slide, cancel_assignment, change_assignment_deadline,
    create_assignments, deadline_status,
)
from .models import (
    Assignment, AssignmentHistory, Employee, Question, QuestionOption,
    Notification, Quiz, QuizAttempt, Slide, Training, TrainingVersion,
)
from .quizzes import submit_quiz


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test", ORG_TIME_ZONE="America/New_York")
class AssignmentAdministrationTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(
            name="Demo Administrator", email="demo.admin@example.test",
            role=Employee.Role.ADMINISTRATOR, is_demo=True,
        )
        self.alice = Employee.objects.create(name="Alice", email="alice@example.test", is_demo=True)
        self.bob = Employee.objects.create(name="Bob", email="bob@example.test", is_demo=True)
        self.carol = Employee.objects.create(name="Carol", email="carol@example.test", is_demo=True)
        self.dave = Employee.objects.create(name="Dave", email="dave@example.test", is_demo=True)
        self.erin = Employee.objects.create(name="Erin", email="erin@example.test", is_demo=True)
        self.frank = Employee.objects.create(name="Frank", email="frank@example.test", is_demo=True)
        self.now = timezone.now()
        self.today = self.now.astimezone(ZoneInfo("America/New_York")).date()
        self.training = Training.objects.create()
        self.version = self.publish_version(self.training, 1, "Safety")

    def publish_version(self, training, number, title, *, with_quiz=False):
        version = TrainingVersion.objects.create(
            training=training, version_number=number, title=title,
        )
        Slide.objects.create(version=version, position=1, header="Read", body="First slide")
        Slide.objects.create(version=version, position=2, header="Apply", body="Second slide")
        if with_quiz:
            quiz = Quiz.objects.create(version=version, passing_percent=100)
            question = Question.objects.create(quiz=quiz, position=1, type=Question.Type.SINGLE_CHOICE,
                                               prompt="Which action is safe?")
            QuestionOption.objects.create(question=question, position=1, text="Report it", is_correct=True)
            QuestionOption.objects.create(question=question, position=2, text="Ignore it", is_correct=False)
        version.status = TrainingVersion.Status.PUBLISHED
        version.published_at = self.now
        version.save()
        return version

    def assign(self, employee, *, version=None, due_date=None, now=None):
        version = version or self.version
        due_date = due_date or self.today + timedelta(days=1)
        results = create_assignments(
            operation_id=uuid.uuid4(), actor=self.admin, version_id=version.id,
            employee_ids=[employee.id], due_date_value=due_date, now=now or self.now,
        )
        self.assertEqual(results[0].outcome, "created")
        return results[0].assignment

    def acknowledge_all(self, assignment, employee, *, at=None):
        for slide in assignment.version.slides.all():
            acknowledge_slide(assignment_id=assignment.id, slide_id=slide.id,
                              employee=employee, now=at or self.now)
        assignment.refresh_from_db()

    def test_canceling_assignment_shows_suppressed_reminder_in_notification_detail(self):
        assignment = self.assign(self.alice, due_date=self.today + timedelta(days=5))
        reminder = assignment.notifications.get(kind=Notification.Kind.DUE_DAY_REMINDER)
        response = self.client.post(reverse("cancel_assignment", args=[assignment.id]), {
            "reason": "Withdrawn",
        })
        self.assertEqual(response.status_code, 302)
        detail = self.client.get(reverse("notification_detail", args=[reminder.id]))
        self.assertContains(detail, "Suppressed")
        self.assertNotContains(detail, "Queued for delivery")

    def test_admin_assignment_actions_share_one_error_mapping(self):
        assignment = self.assign(self.alice)
        actions = (
            ("change_deadline", "change_assignment_deadline"),
            ("cancel_assignment", "cancel_assignment"),
            ("waive_assignment", "waive_assignment"),
            ("archive_assignment", "archive_assignment"),
            ("restore_assignment", "restore_assignment"),
        )
        errors = ((AssignmentInputError, 400), (AssignmentConflict, 409), (AssignmentAccessError, 403))
        for route, function in actions:
            for error_type, status in errors:
                with self.subTest(route=route, error=error_type.__name__):
                    message = f"{error_type.__name__} detail"
                    with patch(f"training.assignment_admin_views.{function}", side_effect=error_type(message)):
                        response = self.client.post(reverse(route, args=[assignment.id]),
                                                    {"reason": "Reason", "due_date": self.today.isoformat()})
                    self.assertContains(response, message, status_code=status)
                    self.assertContains(response, "Assignment", status_code=status)

    def test_dashboard_filters_authoritative_progress_deadline_and_employee_activity(self):
        alice_assignment = self.assign(self.alice)
        bob_assignment = self.assign(self.bob)
        self.bob.is_active = False
        self.bob.save(update_fields=["is_active"])
        past = self.today - timedelta(days=1)
        historical_now = self.now - timedelta(days=3)
        carol_assignment = self.assign(self.carol, due_date=past, now=historical_now)
        acknowledge_slide(assignment_id=carol_assignment.id,
                          slide_id=self.version.slides.first().id, employee=self.carol, now=historical_now)
        dave_assignment = self.assign(self.dave)
        self.acknowledge_all(dave_assignment, self.dave)
        erin_assignment = self.assign(self.erin, due_date=past, now=historical_now)
        self.acknowledge_all(erin_assignment, self.erin, at=self.now)
        frank_assignment = self.assign(self.frank, due_date=past, now=historical_now)
        cancel_assignment(assignment_id=frank_assignment.id, actor=self.admin, reason="Superseded", now=self.now)

        default = self.client.get(reverse("assignment_dashboard"))
        self.assertEqual(default.status_code, 200)
        self.assertEqual({row["assignment"].id for row in default.context["rows"]},
                         {alice_assignment.id, carol_assignment.id})
        self.assertEqual(default.context["rows"][0]["assignment"].id, carol_assignment.id)
        self.assertContains(default, "Results · 2 assignments")
        self.assertNotContains(default, "State: Pending")
        self.assertContains(default, reverse("assignment_admin_detail", args=[carol_assignment.id]))
        all_rows = self.client.get(reverse("assignment_dashboard"), {"state": "all", "activity": "all"})
        self.assertEqual(len(all_rows.context["rows"]), 6)
        self.assertContains(all_rows, "N/A")
        self.assertEqual({row["assignment"].id for row in self.client.get(reverse("assignment_dashboard"), {
            "state": "all", "activity": "all", "progress": "in_progress",
        }).context["rows"]}, {carol_assignment.id})
        self.assertEqual({row["assignment"].id for row in self.client.get(reverse("assignment_dashboard"), {
            "state": "all", "activity": "all", "deadline": "overdue",
        }).context["rows"]}, {carol_assignment.id})
        self.assertEqual({row["assignment"].id for row in self.client.get(reverse("assignment_dashboard"), {
            "state": "completed", "activity": "all", "deadline": "completed_late",
        }).context["rows"]}, {erin_assignment.id})
        self.assertEqual({row["assignment"].id for row in self.client.get(reverse("assignment_dashboard"), {
            "state": "canceled", "activity": "all",
        }).context["rows"]}, {frank_assignment.id})
        self.assertEqual({row["assignment"].id for row in self.client.get(reverse("assignment_dashboard"), {
            "activity": "inactive", "employee": str(self.bob.id), "training": str(self.training.id),
        }).context["rows"]}, {bob_assignment.id})
        detail = self.client.get(reverse("assignment_admin_detail", args=[bob_assignment.id]))
        self.assertContains(detail, "Inactive employee")
        self.assertContains(detail, "Not acknowledged")
        self.assertEqual(self.client.get(reverse("assignment_dashboard"), {"employee": "bad"}).status_code, 400)

    def test_deadline_change_preserves_old_value_and_employee_sees_new_status(self):
        old_date = self.today - timedelta(days=1)
        assignment = self.assign(self.alice, due_date=old_date, now=self.now - timedelta(days=3))
        self.assertEqual(deadline_status(assignment, now=self.now), "overdue")
        new_date = self.today + timedelta(days=2)
        response = self.client.post(reverse("change_deadline", args=[assignment.id]), {
            "due_date": new_date.isoformat(),
        })
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(assignment.due_date, new_date)
        self.assertEqual(assignment.due_revision, 1)
        self.assertEqual(deadline_status(assignment, now=self.now), "on_time")
        event = assignment.history.get()
        self.assertEqual((event.old_due_date, event.new_due_date), (old_date, new_date))
        self.assertEqual((event.old_due_revision, event.new_due_revision), (0, 1))
        self.assertEqual(event.actor_id, self.admin.id)
        self.assertContains(self.client.get(reverse("assignment_admin_detail", args=[assignment.id])), "Deadline revision 0 → 1")
        self.assertEqual(self.client.post(reverse("change_deadline", args=[assignment.id]), {
            "due_date": new_date.isoformat(),
        }).status_code, 302)
        self.assertEqual(AssignmentHistory.objects.count(), 1)
        self.assertEqual(self.client.post(reverse("change_deadline", args=[assignment.id]), {
            "due_date": old_date.isoformat(),
        }).status_code, 400)
        self.assertEqual(AssignmentHistory.objects.count(), 1)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        my_training = self.client.get(reverse("my_training"))
        self.assertContains(my_training, "On time")
        self.assertContains(my_training, new_date.strftime("%Y"))

    def test_unchanged_overdue_date_is_a_noop(self):
        old_date = self.today - timedelta(days=1)
        assignment = self.assign(self.alice, due_date=old_date, now=self.now - timedelta(days=3))
        updated, event = change_assignment_deadline(
            assignment_id=assignment.id, actor=self.admin,
            due_date_value=old_date.isoformat(), now=self.now,
        )
        self.assertIsNone(event)
        self.assertEqual(updated.due_revision, 0)
        self.assertEqual(AssignmentHistory.objects.count(), 0)

    def test_dashboard_shows_latest_and_passing_quiz_scores(self):
        quiz_training = Training.objects.create()
        version = self.publish_version(quiz_training, 1, "Quiz training", with_quiz=True)
        assignment = self.assign(self.alice, version=version)
        self.acknowledge_all(assignment, self.alice)
        question = version.quiz.questions.get()
        wrong = question.options.get(is_correct=False)
        right = question.options.get(is_correct=True)
        submit_quiz(assignment_id=assignment.id, employee=self.alice,
                    submission_id=uuid.uuid4(), answers={question.id: wrong.id})
        submit_quiz(assignment_id=assignment.id, employee=self.alice,
                    submission_id=uuid.uuid4(), answers={question.id: right.id})
        response = self.client.get(reverse("assignment_dashboard"), {"state": "completed"})
        row = next(row for row in response.context["rows"] if row["assignment"].id == assignment.id)
        self.assertEqual(row["latest_score"], "100.0%")
        self.assertEqual(row["passing_score"], "100.0%")
        detail = self.client.get(reverse("assignment_admin_detail", args=[assignment.id]))
        self.assertContains(detail, "Which action is safe?")
        self.assertContains(detail, "Ignore it")
        self.assertContains(detail, "Report it")

    def test_cancellation_preserves_failed_attempt_and_replacement_starts_fresh(self):
        quiz_training = Training.objects.create()
        first_version = self.publish_version(quiz_training, 1, "Security", with_quiz=True)
        assignment = self.assign(self.alice, version=first_version)
        self.acknowledge_all(assignment, self.alice)
        self.assertIsNone(assignment.completed_at)
        question = first_version.quiz.questions.get()
        wrong = question.options.get(is_correct=False)
        failed = submit_quiz(assignment_id=assignment.id, employee=self.alice,
                             submission_id=uuid.uuid4(), answers={question.id: wrong.id})
        self.assertFalse(failed.passed)
        self.assertEqual(self.client.post(reverse("cancel_assignment", args=[assignment.id]), {
            "reason": "Material replaced",
        }).status_code, 302)
        assignment.refresh_from_db()
        self.assertIsNotNone(assignment.canceled_at)
        cancellation = assignment.history.get()
        self.assertIsNone(cancellation.old_canceled_at)
        self.assertEqual(cancellation.new_canceled_at, assignment.canceled_at)
        self.assertEqual(cancellation.reason, "Material replaced")
        self.assertEqual(assignment.slide_progress.count(), 2)
        self.assertEqual(QuizAttempt.objects.filter(assignment=assignment).count(), 1)
        self.assertContains(self.client.get(reverse("assignment_admin_detail", args=[assignment.id])), "Material replaced")
        self.assertContains(self.client.get(reverse("assignment_admin_detail", args=[assignment.id])), "Did not pass")
        with self.assertRaises(AssignmentAccessError):
            submit_quiz(assignment_id=assignment.id, employee=self.alice,
                        submission_id=uuid.uuid4(), answers={question.id: wrong.id})
        with self.assertRaises(AssignmentAccessError):
            acknowledge_slide(assignment_id=assignment.id,
                              slide_id=first_version.slides.first().id, employee=self.alice)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        self.assertEqual(self.client.get(reverse("assignment_detail", args=[assignment.id])).status_code, 404)
        self.assertEqual(self.client.post(reverse("submit_quiz", args=[assignment.id]), {
            "submission_id": str(uuid.uuid4()), f"answer_{question.id}": str(wrong.id),
        }).status_code, 403)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.admin.id)})
        second_version = self.publish_version(quiz_training, 2, "Updated Security")
        replacement = self.assign(self.alice, version=second_version)
        self.assertNotEqual(replacement.id, assignment.id)
        self.assertEqual(replacement.slide_progress.count(), 0)
        self.assertEqual(replacement.quiz_attempts.count(), 0)
        self.assertIsNone(replacement.started_at)
        self.assertIsNone(replacement.completed_at)
        self.assertEqual(Assignment.objects.get(pk=assignment.id).version_id, first_version.id)

    def test_completion_and_cancellation_each_block_the_losing_transition(self):
        completed_first = self.assign(self.alice)
        self.acknowledge_all(completed_first, self.alice)
        with self.assertRaises(AssignmentConflict):
            cancel_assignment(assignment_id=completed_first.id, actor=self.admin, reason="Too late")
        with self.assertRaises(AssignmentConflict):
            change_assignment_deadline(assignment_id=completed_first.id, actor=self.admin,
                                       due_date_value=(self.today + timedelta(days=2)).isoformat())
        self.assertIsNone(Assignment.objects.get(pk=completed_first.id).canceled_at)
        canceled_first = self.assign(self.bob)
        cancel_assignment(assignment_id=canceled_first.id, actor=self.admin, reason="Withdrawn")
        with self.assertRaises(AssignmentAccessError):
            acknowledge_slide(assignment_id=canceled_first.id,
                              slide_id=self.version.slides.first().id, employee=self.bob)
        self.assertIsNone(Assignment.objects.get(pk=canceled_first.id).completed_at)
        self.assertEqual(AssignmentHistory.objects.filter(assignment=canceled_first).count(), 1)

    def test_mutations_validate_reason_due_date_and_administrator_access(self):
        assignment = self.assign(self.alice)
        with self.assertRaises(AssignmentInputError):
            cancel_assignment(assignment_id=assignment.id, actor=self.admin, reason=" ")
        with self.assertRaises(AssignmentInputError):
            change_assignment_deadline(assignment_id=assignment.id, actor=self.admin,
                                       due_date_value=(self.today - timedelta(days=1)).isoformat(), now=self.now)
        self.assertEqual(AssignmentHistory.objects.count(), 0)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        self.assertEqual(self.client.get(reverse("assignment_dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("assignment_admin_detail", args=[assignment.id])).status_code, 403)
        self.assertEqual(self.client.post(reverse("change_deadline", args=[assignment.id]), {
            "due_date": (self.today + timedelta(days=2)).isoformat(),
        }).status_code, 403)
        self.assertEqual(self.client.post(reverse("cancel_assignment", args=[assignment.id]), {
            "reason": "Unauthorized",
        }).status_code, 403)
        self.assertIsNone(Assignment.objects.get(pk=assignment.id).canceled_at)
