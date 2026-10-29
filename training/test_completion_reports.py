import csv
import io
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignment_reports import ReportFilterError
from .completion_reports import CompletionRequest, evidence, population, slide_summaries, summaries, totals
from .models import Assignment, AssignmentSlideProgress, Employee, Quiz, QuizAttempt, Slide, Training, TrainingVersion


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="admin@example.test", ORG_TIME_ZONE="America/New_York")
class CompletionReportsTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.now = timezone.now()
        self.admin = Employee.objects.create(name="Admin", email="admin@example.test", is_demo=True,
                                             role="administrator")
        self.employee = Employee.objects.create(name="=Morgan", email="morgan@example.test", is_demo=True)
        self.training = Training.objects.create(is_demo=True)
        self.version = self.publish(self.training, "Safety", 1)

    def publish(self, training, title, number):
        version = TrainingVersion.objects.create(training=training, title=title, version_number=number)
        Slide.objects.create(version=version, position=1, header="Read", body="Read this")
        Slide.objects.create(version=version, position=2, header="Practice", body="Practice this")
        version.status = "published"
        version.save()
        return version

    def assignment(self, *, employee=None, version=None, **changes):
        version = version or self.version
        return Assignment.objects.create(employee=employee or self.employee, version=version,
                                         training=version.training, due_date=self.now.date(), due_timezone="UTC",
                                         due_at=self.now - timedelta(hours=1), **changes)

    def query(self, **params):
        scope = CompletionRequest.parse(params)
        return scope, population(scope, is_demo=True, now=self.now)

    def test_population_preserves_cycles_and_excludes_exceptions_inactive_and_real_people(self):
        done = self.assignment(completed_at=self.now, cycle_number=1)
        pending = self.assignment(renews=done, cycle_number=2)
        self.assignment(canceled_at=self.now)
        self.assignment(waived_at=self.now)
        self.assignment(completed_at=self.now, archived_at=self.now)
        inactive = Employee.objects.create(name="Inactive", email="inactive@example.test", is_demo=True, is_active=False)
        self.assignment(employee=inactive)
        real = Employee.objects.create(name="Real", email="real@example.test", is_demo=False)
        self.assignment(employee=real)
        scope, assignments = self.query()
        self.assertEqual(set(assignments.values_list("pk", flat=True)), {done.pk, pending.pk})
        self.assertEqual(totals(assignments, scope), {"total": 2, "completed": 1, "pending": 1,
                                                     "overdue": 1, "employees": 1, "rate": 50.0})
        self.assertEqual(self.query(activity="all")[1].count(), 3)
        for mode in ("canceled", "waived", "archived"):
            history_scope, history = self.query(mode=mode)
            self.assertEqual(history.count(), 1)
            self.assertIsNone(totals(history, history_scope)["rate"])

    def test_empty_entities_are_retained_without_search_and_drafts_are_excluded(self):
        draft = Training.objects.create(is_demo=True)
        TrainingVersion.objects.create(training=draft, version_number=1, title="Draft")
        scope, assignments = self.query()
        rows = list(summaries(assignments, scope, is_demo=True))
        self.assertEqual([row.pk for row in rows], [self.training.pk])
        self.assertEqual(rows[0].total, 0)
        self.assertIsNone(rows[0].rate)
        scope, assignments = self.query(q="missing")
        self.assertEqual(list(summaries(assignments, scope, is_demo=True)), [])
        scope, assignments = self.query(view="employee")
        self.assertEqual(summaries(assignments, scope, is_demo=True).count(), 2)

    def test_summary_counts_do_not_multiply_acknowledgments_or_quiz_attempts(self):
        # Attempts are evidence, never extra assignment cycles.
        assignment = self.assignment(completed_at=self.now)
        for slide in self.version.slides.all():
            AssignmentSlideProgress.objects.create(assignment=assignment, slide=slide, acknowledged_at=self.now)
        scope, assignments = self.query()
        with self.assertNumQueries(1):
            row = list(summaries(assignments, scope, is_demo=True))[0]
        self.assertEqual((row.total, row.completed, row.employees, row.rate), (1, 1, 1, 100))
        self.assertEqual(evidence(assignments).get().acknowledged, 2)

    def test_version_specific_slides_include_zero_and_drilldown_keeps_full_progress(self):
        assignment = self.assignment()
        slides = list(self.version.slides.all())
        AssignmentSlideProgress.objects.create(assignment=assignment, slide=slides[0], acknowledged_at=self.now)
        scope, assignments = self.query()
        rows = slide_summaries(assignments, self.version.pk)
        self.assertEqual([(r["acknowledged"], r["total"]) for r in rows], [(1, 1), (0, 1)])
        scope = CompletionRequest.parse({"version": str(self.version.pk), "slide": str(slides[0].pk)}, training=self.training)
        self.assertEqual(evidence(population(scope, is_demo=True, now=self.now, training=self.training)).get().slide_total, 2)
        other = self.publish(self.training, "Safety new", 2)
        self.assertTrue(all(row["acknowledged"] == 0 for row in slide_summaries(assignments, other.pk)))

    def test_request_rejects_invalid_and_cross_training_versions_and_slides(self):
        for params in ({"activity": "bad"}, {"mode": "all"}, {"sort": "email"}, {"version": "bad"},
                       {"tab": "slide"}, {"version": str(self.version.pk)}):
            with self.assertRaises(ReportFilterError):
                CompletionRequest.parse(params)
        other = Training.objects.create(is_demo=True)
        with self.assertRaises(ReportFilterError):
            CompletionRequest.parse({"version": str(self.version.pk)}, training=other)

    def test_overview_totals_and_export_include_beyond_page_one(self):
        for index in range(27):
            training = Training.objects.create(is_demo=True)
            version = self.publish(training, f"Course {index:02}", 1)
            self.assignment(version=version, completed_at=self.now if index < 18 else None)
        response = self.client.get(reverse("reports"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["total"], 27)
        self.assertEqual(response.context["summary"]["completed"], 18)
        self.assertEqual(len(response.context["rows"]), 25)
        exported = self.client.get(reverse("reports_export"))
        rows = list(csv.reader(io.StringIO(exported.content.decode())))
        self.assertEqual(len(rows), 29)  # header + 28 published trainings, including the empty one
        self.assertEqual(sum(int(row[9]) for row in rows[1:]), 27)
        self.assertEqual(rows[1][1], "America/New_York")

    def test_search_scope_and_csv_formula_escaping_match(self):
        self.assignment()
        response = self.client.get(reverse("reports"), {"q": "morgan", "view": "employee"})
        self.assertEqual(response.context["summary"]["total"], 1)
        self.assertEqual(len(response.context["rows"]), 1)
        exported = self.client.get(reverse("reports_export"), {"q": "morgan", "view": "employee"})
        self.assertIn("'=Morgan", exported.content.decode())
        self.assertNotIn("Admin", exported.content.decode())

    def test_training_and_employee_details_keep_historical_titles_and_cycles(self):
        done = self.assignment(completed_at=self.now)
        version = self.publish(self.training, "Renamed safety", 2)
        self.assignment(version=version, renews=done, cycle_number=2)
        response = self.client.get(reverse("employee_report", args=[self.employee.pk]))
        self.assertContains(response, "Safety")
        self.assertContains(response, "Renamed safety")
        self.assertEqual(response.context["summary"]["total"], 2)
        export = self.client.get(reverse("employee_report_export", args=[self.employee.pk]))
        self.assertEqual(len(list(csv.reader(io.StringIO(export.content.decode())))), 3)
        response = self.client.get(reverse("training_report", args=[self.training.pk]), {"version": str(self.version.pk)})
        self.assertEqual(response.context["summary"]["total"], 1)
        self.employee.is_active = False
        self.employee.save()
        response = self.client.get(reverse("employee_report", args=[self.employee.pk]))
        self.assertEqual(response.context["scope"].activity, "inactive")
        self.assertEqual(response.context["summary"]["total"], 2)

    def test_slides_require_version_and_export_matches_visible_population(self):
        self.assignment()
        url = reverse("training_report", args=[self.training.pk])
        response = self.client.get(url, {"tab": "slide"})
        self.assertContains(response, "Choose one published version")
        export = reverse("training_report_export", args=[self.training.pk])
        self.assertEqual(self.client.get(export, {"tab": "slide"}).status_code, 400)
        params = {"tab": "slide", "version": str(self.version.pk)}
        response = self.client.get(url, params)
        self.assertEqual(len(response.context["rows"]), 2)
        rows = list(csv.reader(io.StringIO(self.client.get(export, params).content.decode())))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][7], str(self.version.pk))
        self.assertEqual(rows[1][-2], "1")

    def test_invalid_scope_is_400_and_learner_cannot_view_or_export(self):
        urls = [reverse("reports"), reverse("reports_export"),
                reverse("training_report", args=[self.training.pk]),
                reverse("training_report_export", args=[self.training.pk]),
                reverse("employee_report", args=[self.employee.pk]),
                reverse("employee_report_export", args=[self.employee.pk])]
        for url in urls:
            self.assertEqual(self.client.get(url, {"activity": "invalid"}).status_code, 400)
        session = self.client.session
        session["demo_employee_id"] = str(self.employee.pk)
        session.save()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_library_labels_both_version_summaries_and_sorts(self):
        draft = TrainingVersion.objects.create(training=self.training, version_number=2, title="Changed title")
        Quiz.objects.create(version=draft)
        response = self.client.get(reverse("training_list"), {"sort": "-title"})
        self.assertContains(response, "Published v1: 2 slides")
        self.assertContains(response, "Draft v2: 0 slides")
        self.assertContains(response, "Import training drafts")
        self.assertEqual(response.context["browse"].params["sort"], "-title")

    def test_all_acknowledgments_with_failed_quiz_remain_pending_and_latest_pass_is_separate(self):
        import uuid
        version = TrainingVersion.objects.create(training=self.training, version_number=2, title="With quiz")
        slide = Slide.objects.create(version=version, position=1, body="Read")
        quiz = Quiz.objects.create(version=version, passing_percent=80)
        version.status = "published"
        version.save()
        assignment = self.assignment(version=version, started_at=self.now)
        AssignmentSlideProgress.objects.create(assignment=assignment, slide=slide, acknowledged_at=self.now)
        QuizAttempt.objects.create(assignment=assignment, quiz=quiz, submission_id=uuid.uuid4(),
                                   request_fingerprint="failed", correct_count=1, question_count=2,
                                   passed=False, submitted_at=self.now)
        response = self.client.get(reverse("training_report", args=[self.training.pk]))
        row = response.context["rows"][0]
        self.assertEqual((row.state, row.latest_score, row.assignment.acknowledged, row.assignment.slide_total),
                         ("Pending", "50.0%", 1, 1))
        self.assertEqual(response.context["summary"]["rate"], 0)
        QuizAttempt.objects.create(assignment=assignment, quiz=quiz, submission_id=uuid.uuid4(),
                                   request_fingerprint="passed", correct_count=2, question_count=2,
                                   passed=True, submitted_at=self.now + timedelta(seconds=1))
        assignment.completed_at = self.now + timedelta(seconds=1)
        assignment.save()
        response = self.client.get(reverse("training_report", args=[self.training.pk]))
        self.assertEqual(response.context["rows"][0].latest_score, "100.0%")
        self.assertEqual(response.context["summary"]["total"], 1)
        self.assertEqual(response.context["summary"]["rate"], 100)

    def test_assignment_back_links_accept_only_known_report_routes(self):
        assignment = self.assignment()
        url = reverse("assignment_admin_detail", args=[assignment.pk])
        report = reverse("training_report", args=[self.training.pk]) + "?mode=eligible&q=safety&page=2"
        response = self.client.get(url, {"return_to": report})
        self.assertEqual(response.context["back_url"], report)
        for target in ("https://example.test/reports/", "/reports/unknown/", "//example.test/reports/", "javascript:alert(1)"):
            response = self.client.get(url, {"return_to": target})
            self.assertEqual(response.context["back_url"], reverse("assignment_dashboard"))

    def test_history_rates_are_not_described_as_empty_and_no_duplicate_search_control(self):
        self.assignment(canceled_at=self.now)
        response = self.client.get(reverse("reports"), {"mode": "canceled"})
        self.assertNotContains(response, "No assignments")
        self.assertIsNone(response.context["summary"]["rate"])
        self.assertNotContains(response, "Search trainings")
        self.assertEqual(response.content.decode().count('id="report-search"'), 1)

    def test_detail_query_count_is_bounded_by_page_not_row_count(self):
        from .assignment_reports import report_rows
        for index in range(8):
            self.assignment(completed_at=self.now, cycle_number=index + 1)
        scope, assignments = self.query()
        with self.assertNumQueries(2):
            rows = report_rows(evidence(assignments), now=self.now)
        self.assertEqual(len(rows), 8)

    def test_sort_uses_actual_rates_and_stable_names(self):
        first = self.assignment(completed_at=self.now)
        self.assignment(renews=first, cycle_number=2)
        other = Training.objects.create(is_demo=True)
        version = self.publish(other, "Alpha", 1)
        self.assignment(version=version, completed_at=self.now)
        scope, assignments = self.query(sort="-rate")
        self.assertEqual([row.pk for row in summaries(assignments, scope, is_demo=True)], [other.pk, self.training.pk])
        scope, assignments = self.query(sort="rate")
        self.assertEqual([row.pk for row in summaries(assignments, scope, is_demo=True)], [self.training.pk, other.pk])
        response = self.client.get(reverse("training_list"), {"sort": "-title"})
        self.assertEqual([row["training"].pk for row in response.context["rows"]], [self.training.pk, other.pk])
