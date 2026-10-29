from datetime import timedelta
from unittest.mock import patch
from xml.etree import ElementTree

from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin_chart import pending_load_chart
from .assignment_reports import ReportFilters, export_rows, report_assignments, report_rows
from .models import Assignment, Employee, Training, TrainingVersion


class PendingLoadChartTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.safety = Training.objects.create(is_demo=True)
        self.safety_version = TrainingVersion.objects.create(
            training=self.safety, version_number=1, title="Safety",
            status=TrainingVersion.Status.PUBLISHED,
        )
        self.privacy = Training.objects.create(is_demo=True)
        self.privacy_version = TrainingVersion.objects.create(
            training=self.privacy, version_number=1, title="Privacy",
            status=TrainingVersion.Status.PUBLISHED,
        )

    def assignment(self, name, version, *, overdue=False, started=False):
        employee = Employee.objects.create(name=name, email=f"{name.lower()}@example.test", is_demo=True)
        return Assignment.objects.create(
            employee=employee, training=version.training, version=version,
            due_date=self.now.date(), due_timezone="UTC",
            due_at=self.now - timedelta(days=1) if overdue else self.now + timedelta(days=1),
            started_at=self.now - timedelta(days=2) if started else None,
        )

    def test_filtered_chart_totals_and_segment_counts_match_assignments(self):
        self.assignment("Alice", self.safety_version, overdue=True)
        self.assignment("Bob", self.safety_version, overdue=True, started=True)
        self.assignment("Carol", self.safety_version, started=True)
        self.assignment("Drew", self.privacy_version)
        self.assignment("Erin", self.privacy_version, overdue=True)
        queryset = report_assignments(ReportFilters(), is_demo=True, now=self.now)
        chart = pending_load_chart(queryset, {self.safety.id: "Safety", self.privacy.id: "Privacy"})

        self.assertEqual((chart["total_assignments"], chart["total_overdue"]), (5, 3))
        self.assertEqual([(row["name"], row["total"]) for row in chart["rows"]],
                         [("Safety", 3), ("Privacy", 2)])
        self.assertEqual([row["end_label"] for row in chart["rows"]], ["3", "2"])
        self.assertEqual(chart["title"], "Pending assignments by training")
        self.assertEqual(chart["rows"][0]["parts"], {
            "overdue_not_started": 1, "overdue_in_progress": 1,
            "on_time_not_started": 0, "on_time_in_progress": 1,
        })
        self.assertEqual(sum(segment["value"] for segment in chart["rows"][0]["segments"]), 3)

        html = render_to_string("training/_assignment_relay_chart.html", {"chart": chart})
        svg = html[html.index("<svg"):html.index("</svg>") + 6]
        root = ElementTree.fromstring(svg)
        rectangles = root.findall(".//{http://www.w3.org/2000/svg}rect")
        self.assertEqual(sum(rect.get("stroke") == "#C99A2E" for rect in rectangles), 0)
        self.assertTrue(all(segment["text"] in {"", str(segment["value"])}
                            for row in chart["rows"] for segment in row["segments"]))
        self.assertIn("Safety", html)
        self.assertIn("Privacy", html)
        self.assertNotIn("Source:", html)
        self.assertNotIn("match filters", html)

    def test_chart_uses_every_matching_record_not_just_first_page(self):
        for index in range(27):
            self.assignment(f"Learner{index}", self.safety_version)
        chart = pending_load_chart(report_assignments(ReportFilters(), is_demo=True, now=self.now),
                                   {self.safety.id: "Safety"})
        self.assertEqual(chart["total_assignments"], 27)
        self.assertEqual(chart["rows"][0]["total"], 27)

    @override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
    def test_report_filters_chart_and_export_select_the_same_pending_assignments(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        Employee.objects.create(name="Administrator", email="demo.admin@example.test",
                                role=Employee.Role.ADMINISTRATOR, is_demo=True)
        alice = self.assignment("Alice", self.safety_version, overdue=True)
        bob = self.assignment("Bob", self.safety_version, started=True)
        carol = self.assignment("Carol", self.privacy_version, overdue=True, started=True)
        inactive = self.assignment("Drew", self.privacy_version)
        inactive.employee.is_active = False
        inactive.employee.save()
        archived = self.assignment("Erin", self.privacy_version)
        archived.archived_at = self.now
        archived.save()
        ended = self.assignment("Fred", self.safety_version)
        ended.completed_at = self.now
        ended.save()
        cases = (
            ({}, {alice.id, bob.id, carol.id}),
            ({"activity": "all"}, {alice.id, bob.id, carol.id, inactive.id}),
            ({"activity": "inactive"}, {inactive.id}),
            ({"training": str(self.safety.id), "deadline": "overdue"}, {alice.id}),
            ({"employee": str(bob.employee_id), "progress": "in_progress"}, {bob.id}),
            ({"q": "carol", "deadline": "overdue", "progress": "in_progress"}, {carol.id}),
            ({"q": "Safety"}, {alice.id, bob.id}),
        )
        labels = {self.safety.id: "Safety", self.privacy.id: "Privacy"}
        for params, expected_ids in cases:
            with self.subTest(params=params):
                filters = ReportFilters.from_params(params)
                queryset = report_assignments(filters, is_demo=True, now=self.now)
                chart = pending_load_chart(queryset, labels)
                exported_ids = {row[-1] for row in list(export_rows(filters, is_demo=True, now=self.now, zone_name="UTC"))[1:]}
                self.assertEqual(set(queryset.values_list("id", flat=True)), expected_ids)
                self.assertEqual(exported_ids, {str(item) for item in expected_ids})
                self.assertEqual(sum(row["total"] for row in chart["rows"]), len(expected_ids))
                self.assertEqual(chart["total_assignments"], len(expected_ids))
                response = self.client.get(reverse("assignment_dashboard"), params)
                self.assertEqual(response.context["browse"].total, len(expected_ids))
                self.assertEqual(response.context["chart"]["total_assignments"], len(expected_ids))
                self.assertEqual({row["assignment"].id for row in response.context["rows"]}, expected_ids)
                self.assertEqual(chart["total_overdue"], sum(
                    row["parts"]["overdue_not_started"] + row["parts"]["overdue_in_progress"]
                    for row in chart["rows"]
                ))

    @override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
    def test_due_at_request_clock_is_overdue_in_chart_and_list(self):
        self.assignment("Alice", self.safety_version)
        Assignment.objects.update(due_at=self.now)
        self.client.defaults["HTTP_HOST"] = "localhost"
        Employee.objects.create(name="Administrator", email="demo.admin@example.test",
                                role=Employee.Role.ADMINISTRATOR, is_demo=True)
        with (patch("training.assignment_admin_views.timezone.now", return_value=self.now),
              patch("training.assignment_admin_views.report_assignments", wraps=report_assignments) as query,
              patch("training.assignment_admin_views.report_rows", wraps=report_rows) as list_rows):
            response = self.client.get(reverse("assignment_dashboard"))
        self.assertEqual(query.call_args.kwargs["now"], self.now)
        self.assertEqual(list_rows.call_args.kwargs["now"], self.now)
        self.assertEqual(response.context["chart"]["total_overdue"], 1)
        self.assertEqual(response.context["rows"][0]["deadline"], "Overdue")

    def test_top_eight_and_ties_keep_all_totals_and_full_names_in_table(self):
        for index in range(9):
            training = Training.objects.create(is_demo=True)
            version = TrainingVersion.objects.create(
                training=training, version_number=1, title=f"Training with a long name {index}",
                status=TrainingVersion.Status.PUBLISHED,
            )
            self.assignment(f"Learner{index}", version)
        labels = dict(TrainingVersion.objects.filter(status=TrainingVersion.Status.PUBLISHED)
                      .values_list("training_id", "title"))
        chart = pending_load_chart(report_assignments(ReportFilters(), is_demo=True, now=self.now), labels)
        self.assertEqual((chart["total_rows"], len(chart["rows"]), chart["total_assignments"]), (9, 8, 9))
        self.assertEqual([row["name"] for row in chart["rows"]],
                         [f"Training with a long name {index}" for index in range(8)])
        html = render_to_string("training/_assignment_relay_chart.html", {"chart": chart})
        self.assertIn("Showing 8 of 9 trainings", html)
        self.assertIn("Training with a long name 0</th>", html)

    @override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
    def test_chart_uses_the_current_published_training_label_from_filter_menu(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        Employee.objects.create(name="Administrator", email="demo.admin@example.test",
                                role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.assignment("Alice", self.safety_version)
        TrainingVersion.objects.create(training=self.safety, version_number=2, title="Updated safety",
                                       status=TrainingVersion.Status.PUBLISHED)
        response = self.client.get(reverse("assignment_dashboard"))
        self.assertEqual(response.context["chart"]["rows"][0]["name"], "Updated safety")
        self.assertContains(response, "<option value=\"{}\">Updated safety</option>".format(self.safety.id),
                            html=True)

    @override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
    def test_empty_pending_chart_names_filter_scope_but_other_states_have_no_chart(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        Employee.objects.create(name="Administrator", email="demo.admin@example.test",
                                role=Employee.Role.ADMINISTRATOR, is_demo=True)
        response = self.client.get(reverse("assignment_dashboard"), {"q": "missing", "activity": "inactive"})
        self.assertEqual(response.context["browse"].total, 0)
        self.assertIsNone(response.context["chart"])
        self.assertContains(response, "No pending assignments match these filters.")
        self.assertContains(response, "Activity: Inactive")
        self.assertContains(response, "Search: missing")
        other = self.client.get(reverse("assignment_dashboard"), {"state": "completed"})
        self.assertNotContains(other, "Pending assignment overview")
        self.assertNotContains(other, "No pending assignments match these filters.")

    @override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
    def test_dashboard_renders_chart_for_pending_work(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        Employee.objects.create(name="Administrator", email="demo.admin@example.test",
                                role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.assignment("Alice", self.safety_version, overdue=True)
        response = self.client.get(reverse("assignment_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending assignment overview")
        self.assertContains(response, "View chart values")
        self.assertEqual(response.context["chart"]["total_assignments"], 1)
