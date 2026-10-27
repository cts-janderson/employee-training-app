import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .assignment_reports import (
    QUIZ_MODE, ReportFilters, dashboard_report, dashboard_request, quiz_outcome_assignments,
    report_rows,
)
from .models import Assignment, Employee, Quiz, QuizAttempt, Training, TrainingVersion
from .quizzes import quiz_record


class DashboardReportTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.employee = Employee.objects.create(name="Alice", email="alice@example.test", is_demo=True)
        self.training = Training.objects.create()
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        Quiz.objects.create(version=self.version, passing_percent=80)
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.save()
        self.assignment = Assignment.objects.create(
            employee=self.employee, training=self.training, version=self.version,
            due_date=self.now.date(), due_timezone="UTC", due_at=self.now + timedelta(days=1),
        )

    def attempt(self, number, *, passed, score):
        return QuizAttempt.objects.create(
            id=uuid.UUID(int=number), assignment=self.assignment, quiz=self.version.quiz,
            submission_id=uuid.uuid4(), request_fingerprint="x" * 64,
            correct_count=score, question_count=2, passed=passed, submitted_at=self.now,
        )

    def test_quiz_record_breaks_submitted_at_ties_by_id(self):
        self.assertEqual(quiz_record(self.assignment).outcome, "not_attempted")
        first = self.attempt(1, passed=True, score=2)
        last = self.attempt(2, passed=False, score=0)
        record = quiz_record(self.assignment)
        self.assertEqual((record.latest.id, record.passing.id, record.outcome),
                         (last.id, first.id, "passed"))

    def test_report_rows_and_quiz_chart_agree_on_latest_attempt(self):
        self.attempt(1, passed=True, score=2)
        self.attempt(2, passed=False, score=0)
        filters = ReportFilters(state="all")
        assignments = quiz_outcome_assignments(filters, now=self.now)
        row = report_rows(assignments, now=self.now)[0]
        chart = dashboard_report(dashboard_request({"mode": "quiz"}), assignments, [row], now=self.now)["quiz_chart"]
        person = chart["rows"][0]["people"][0]
        self.assertEqual((row.latest_score, row.passing_score, person["latest_score"]),
                         ("0.0%", "100.0%", "0.0%"))

    def test_quiz_mode_ignores_state_progress_and_deadline(self):
        request = dashboard_request({"mode": "quiz", "progress": "bogus", "state": "completed",
                                     "deadline": "bogus"})
        self.assertIsNone(request.error)
        self.assertEqual(request.mode, QUIZ_MODE)
        self.assertEqual((request.filters.state, request.filters.progress, request.filters.deadline),
                         ("all", "all", "all"))

    def test_invalid_filters_echo_submitted_values_with_defaults(self):
        request = dashboard_request({"state": "archived", "deadline": "bogus"})
        self.assertEqual(request.error, "Choose a valid dashboard filter.")
        self.assertEqual(request.form_values["activity"], "all")
        self.assertEqual(request.form_values["deadline"], "bogus")
        quiz = dashboard_request({"mode": "quiz", "state": "archived", "employee": "bad"})
        self.assertEqual(quiz.error, "Choose a valid employee or training filter.")
        self.assertEqual(quiz.form_values["state"], "archived")

    def test_pending_chart_only_for_valid_pending_requests(self):
        for params, expected in (({}, True), ({"state": "completed"}, False),
                                 ({"mode": "quiz"}, False), ({"deadline": "bogus"}, False)):
            with self.subTest(params=params):
                self.assertEqual(dashboard_request(params).shows_pending_chart, expected)

    def test_active_filter_labels_follow_mode(self):
        params = {"state": "all", "progress": "in_progress", "deadline": "overdue",
                  "activity": "all", "employee": str(self.employee.id), "training": str(self.training.id),
                  "q": "Safety"}
        pending = dashboard_request(params)
        context = dashboard_report(pending, Assignment.objects.none(), [], now=self.now)
        self.assertEqual(context["active_filters"], [
            "State: All", "Progress: In Progress", "Deadline: Overdue", "Activity: All",
            "Employee: Alice", "Training: Safety", "Search: Safety",
        ])
        quiz = dashboard_request({**params, "mode": "quiz"})
        quiz_context = dashboard_report(quiz, Assignment.objects.none(), [], now=self.now)
        self.assertEqual(quiz_context["active_filters"], [
            "Activity: All", "Employee: Alice", "Training: Safety", "Search: Safety",
        ])
        unknown = dashboard_request({"state": "all", "training": str(uuid.uuid4())})
        self.assertIn("Training: Unknown training",
                      dashboard_report(unknown, Assignment.objects.none(), [], now=self.now)["active_filters"])
