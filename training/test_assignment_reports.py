from datetime import timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignment_reports import ReportFilterError, ReportFilters, export_rows, report_assignments, report_rows
from .assignments import DEADLINE_LABELS, PROGRESS_LABELS, deadline_status, progress_status
from .models import Assignment, Employee, Quiz, Slide, Training, TrainingVersion


class ReportFixtures:
    def setUp(self):
        self.now = timezone.now()
        self.training = Training.objects.create(is_demo=True)
        self.version = TrainingVersion.objects.create(
            training=self.training, version_number=1, title="Safety",
        )
        Slide.objects.create(version=self.version, position=1, body="Read")
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.save()

    def assignment(self, name, **changes):
        employee = Employee.objects.create(name=name, email=f"{name.lower()}@example.test", is_demo=True)
        assignment = Assignment.objects.create(
            employee=employee, training=self.training, version=self.version,
            due_date=self.now.date(), due_timezone="UTC", due_at=self.now + timedelta(days=1),
        )
        for field, value in changes.items():
            setattr(assignment, field, value)
        assignment.save()
        return assignment


class AssignmentReportTests(ReportFixtures, TestCase):
    def test_filters_reject_unknown_values_with_dashboard_messages(self):
        with self.assertRaisesMessage(ReportFilterError, "Choose a valid dashboard filter."):
            ReportFilters.from_params({"progress": "unknown"})
        with self.assertRaisesMessage(ReportFilterError, "Choose a valid employee or training filter."):
            ReportFilters.from_params({"employee": "bad"})

    def test_archived_state_defaults_activity_to_all(self):
        self.assertEqual(ReportFilters.from_params({"state": "archived"}).activity, "all")

    def test_sql_progress_deadline_and_order_match_assignment_rules(self):
        pending = self.assignment("Pending")
        overdue = self.assignment("Overdue", due_at=self.now - timedelta(days=1))
        started = self.assignment("Started", started_at=self.now)
        completed = self.assignment("Completed", completed_at=self.now)
        late = self.assignment("Late", due_at=self.now - timedelta(days=1), completed_at=self.now)
        canceled = self.assignment("Canceled", canceled_at=self.now)
        archived = self.assignment("Archived", canceled_at=self.now, archived_at=self.now)
        rows = report_rows(report_assignments(ReportFilters(state="all", activity="all"), is_demo=True, now=self.now), now=self.now)
        self.assertEqual(rows[0].assignment.id, overdue.id)
        self.assertEqual({row.assignment.id for row in rows},
                         {pending.id, overdue.id, started.id, completed.id, late.id, canceled.id})
        for row in rows:
            self.assertEqual(row.progress, PROGRESS_LABELS[progress_status(row.assignment)])
            expected = "—" if row.assignment.canceled_at else DEADLINE_LABELS[deadline_status(row.assignment,
                                                                                                  now=self.now)]
            self.assertEqual(row.deadline, expected)
        archived_rows = report_rows(report_assignments(ReportFilters(state="archived", activity="all"),
                                                      is_demo=True, now=self.now), now=self.now)
        self.assertEqual([row.assignment.id for row in archived_rows], [archived.id])

    def test_scores_show_na_without_quiz_and_dash_before_first_attempt(self):
        self.assignment("NoQuiz")
        rows = report_rows(report_assignments(ReportFilters(), is_demo=True, now=self.now), now=self.now)
        self.assertEqual((rows[0].latest_score, rows[0].passing_score), ("N/A", "N/A"))
        with_quiz = TrainingVersion.objects.create(training=self.training, version_number=2, title="Safety quiz")
        Quiz.objects.create(version=with_quiz, passing_percent=80)
        with_quiz.status = TrainingVersion.Status.PUBLISHED
        with_quiz.save()
        self.version = with_quiz
        self.assignment("Quiz")
        rows = report_rows(report_assignments(ReportFilters(), is_demo=True, now=self.now), now=self.now)
        self.assertEqual({(row.latest_score, row.passing_score) for row in rows},
                         {("N/A", "N/A"), ("—", "—")})

    def test_export_uses_report_rows_and_neutralises_formula_prefixes(self):
        first = self.assignment("=HYPERLINK")
        second = self.assignment("+Phone", archived_at=self.now, canceled_at=self.now)
        rows = list(export_rows(ReportFilters(state="all"), is_demo=True, now=self.now, zone_name="America/New_York"))
        self.assertEqual([row[-1] for row in rows[1:]], [str(first.id)])
        self.assertEqual(rows[1][2], "'=HYPERLINK")
        self.assertEqual(rows[1][1], "America/New_York")
        self.assertEqual(rows[1][0], self.now.astimezone(ZoneInfo("America/New_York")).isoformat())
        archived = list(export_rows(ReportFilters(state="archived", activity="all"),
                                    is_demo=True, now=self.now, zone_name="UTC"))
        self.assertEqual([row[-1] for row in archived[1:]], [str(second.id)])
        self.assertEqual(archived[1][2], "'+Phone")


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
class AssignmentExportHttpTests(ReportFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(name="Admin", email="demo.admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)

    def test_invalid_filters_return_400_not_a_file(self):
        response = self.client.get(reverse("export_assignments"), {"state": "bad"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Content-Disposition", response)

    def test_employee_cannot_export(self):
        employee = Employee.objects.create(name="Learner", email="learner@example.test", is_demo=True)
        self.client.post(reverse("demo_switch"), {"employee_id": str(employee.id)})
        self.assertEqual(self.client.get(reverse("export_assignments")).status_code, 403)

    def test_csv_is_downloadable_and_matches_dashboard(self):
        assignment = self.assignment("Alice")
        dashboard = self.client.get(reverse("assignment_dashboard"))
        response = self.client.get(reverse("export_assignments"))
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn(str(assignment.id), content)
        self.assertEqual([row["assignment"].id for row in dashboard.context["rows"]], [assignment.id])
        self.assertContains(dashboard, "Export CSV")
