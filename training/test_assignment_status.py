import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignment_reports import report_rows
from .assignments import AssignmentStatus, assignment_status
from .models import Assignment, Employee, Quiz, QuizAttempt, Training, TrainingVersion


class AssignmentStatusTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.training = Training.objects.create(is_demo=True)
        self.version = TrainingVersion.objects.create(
            training=self.training, version_number=1, title="Status training",
            status=TrainingVersion.Status.PUBLISHED,
        )

    def make_assignment(self, name, *, started=False, ending=None, late=False, archived=False):
        employee = Employee.objects.create(name=name, email=f"{name}@example.test", is_demo=True)
        due_at = self.now - timedelta(days=1) if late else self.now + timedelta(days=1)
        return Assignment.objects.create(
            employee=employee, training=self.training, version=self.version,
            due_date=due_at.date(), due_timezone="UTC", due_at=due_at,
            started_at=self.now - timedelta(days=2) if started else None,
            completed_at=self.now if ending in {"completed", "completed_then_canceled"} else None,
            canceled_at=self.now if ending in {"canceled", "completed_then_canceled"} else None,
            waived_at=self.now if ending == "waived" else None,
            archived_at=self.now if archived else None,
        )

    def cases(self):
        return (
            ("pending_new", {}, ("pending", "not_started", "on_time")),
            ("pending_overdue", {"late": True}, ("pending", "not_started", "overdue")),
            ("started", {"started": True}, ("pending", "in_progress", "on_time")),
            ("started_overdue", {"started": True, "late": True}, ("pending", "in_progress", "overdue")),
            ("completed", {"ending": "completed"}, ("completed", "completed", "on_time")),
            ("completed_late", {"ending": "completed", "late": True},
             ("completed", "completed", "completed_late")),
            ("canceled", {"ending": "canceled"}, ("canceled", "not_started", None)),
            ("canceled_started_archived", {"ending": "canceled", "started": True, "late": True,
                                            "archived": True}, ("canceled", "in_progress", None)),
            ("waived_archived", {"ending": "waived", "late": True, "archived": True},
             ("waived", "not_started", None)),
            ("completed_archived", {"ending": "completed", "archived": True},
             ("completed", "completed", "on_time")),
            ("completed_then_canceled", {"ending": "completed_then_canceled", "archived": True},
             ("canceled", "completed", None)),
        )

    def test_python_and_sql_status_agree(self):
        for name, changes, expected in self.cases():
            with self.subTest(name=name):
                assignment = self.make_assignment(name, **changes)
                python = assignment_status(assignment, now=self.now)
                sql = Assignment.objects.with_status(now=self.now).get(pk=assignment.pk)
                self.assertEqual((python.state, python.progress, python.deadline), expected)
                self.assertEqual(python, AssignmentStatus(
                    sql.status_state, sql.status_progress, sql.status_deadline, sql.archived_at is not None,
                ))

    def test_report_rows_use_status_labels(self):
        for name, changes, _ in self.cases():
            assignment = self.make_assignment(name, **changes)
            status = assignment_status(assignment, now=self.now)
            row = report_rows([assignment], now=self.now)[0]
            self.assertEqual((row.state, row.progress, row.deadline),
                             (status.report_state_label, status.progress_label, status.deadline_label))
            if name == "waived_archived":
                self.assertEqual(row.state, "Archived · Waived")

    def test_due_at_boundary_is_overdue_in_python_and_sql(self):
        assignment = self.make_assignment("boundary")
        Assignment.objects.filter(pk=assignment.pk).update(due_at=self.now)
        assignment.refresh_from_db()
        self.assertEqual(assignment_status(assignment, now=self.now).deadline, "overdue")
        self.assertEqual(Assignment.objects.with_status(now=self.now).get(pk=assignment.pk).status_deadline,
                         "overdue")


    @override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="status.admin@example.test")
    def test_quiz_result_shows_no_deadline_status_for_waived_work(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        Employee.objects.create(name="Admin", email="status.admin@example.test",
                                role=Employee.Role.ADMINISTRATOR, is_demo=True)
        TrainingVersion.objects.filter(pk=self.version.pk).update(status=TrainingVersion.Status.DRAFT)
        quiz = Quiz.objects.create(version=self.version, passing_percent=80)
        TrainingVersion.objects.filter(pk=self.version.pk).update(status=TrainingVersion.Status.PUBLISHED)
        assignment = self.make_assignment("quizresult", late=True, ending="waived")
        attempt = QuizAttempt.objects.create(
            assignment=assignment, quiz=quiz, submission_id=uuid.uuid4(), request_fingerprint="x" * 64,
            correct_count=0, question_count=1, passed=False, submitted_at=self.now,
        )
        self.client.post(reverse("demo_switch"), {"employee_id": str(assignment.employee_id)})
        response = self.client.get(reverse("quiz_result", args=[assignment.id, attempt.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Overdue")
        self.assertContains(response, "· —")
