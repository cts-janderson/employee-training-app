import uuid
from datetime import timedelta
from xml.etree import ElementTree

from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignment_reports import ReportFilters, quiz_outcome_assignments
from .models import Assignment, Employee, Quiz, QuizAttempt, Training, TrainingVersion
from .quiz_outcomes_chart import quiz_outcomes_chart


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
class QuizOutcomesChartTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.now = timezone.now()
        Employee.objects.create(name="Administrator", email="demo.admin@example.test",
                                role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.training = Training.objects.create()
        self.version = TrainingVersion.objects.create(
            training=self.training, version_number=1, title="Safety",
        )
        self.quiz = Quiz.objects.create(version=self.version, passing_percent=70)
        TrainingVersion.objects.filter(pk=self.version.pk).update(status=TrainingVersion.Status.PUBLISHED)
        self.version.refresh_from_db()

    def assignment(self, name, *, version=None, archived=False, canceled=False, inactive=False,
                   cycle=1):
        employee = Employee.objects.create(name=name, email=f"{name.lower()}@example.test",
                                           is_demo=True, is_active=not inactive)
        version = version or self.version
        return Assignment.objects.create(
            employee=employee, training=version.training, version=version, cycle_number=cycle,
            due_date=self.now.date(), due_timezone="UTC", due_at=self.now + timedelta(days=1),
            archived_at=self.now if archived else None,
            canceled_at=self.now if canceled else None,
        )

    def attempt(self, assignment, *, passed, minutes):
        return QuizAttempt.objects.create(
            assignment=assignment, quiz=assignment.version.quiz,
            submission_id=uuid.uuid4(), request_fingerprint="test",
            correct_count=8 if passed else 4, question_count=10,
            passed=passed, submitted_at=self.now + timedelta(minutes=minutes),
        )

    def test_mode_classifies_retries_and_lists_people_behind_every_count(self):
        alice = self.assignment("Alice")
        self.attempt(alice, passed=False, minutes=1)
        self.attempt(alice, passed=True, minutes=2)
        alice.completed_at = self.now + timedelta(minutes=2)
        alice.save()
        bob = self.assignment("Bob")
        self.attempt(bob, passed=False, minutes=3)
        self.assignment("Carol")
        self.assignment("Inactive", inactive=True)
        self.assignment("Archived", archived=True)
        self.assignment("Canceled", canceled=True)
        slide_training = Training.objects.create()
        slide_version = TrainingVersion.objects.create(
            training=slide_training, version_number=1, title="Slide only",
            status=TrainingVersion.Status.PUBLISHED,
        )
        self.assignment("SlideOnly", version=slide_version)

        response = self.client.get(reverse("assignment_dashboard"), {"mode": "quiz", "state": "pending"})
        chart = response.context["quiz_chart"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["browse"].total, 3)
        self.assertIsNone(response.context["chart"])
        self.assertEqual(chart["total_assignments"], 3)
        self.assertEqual(chart["rows"][0]["parts"], {
            "passed": 1, "not_attempted": 1, "retry_needed": 1,
        })
        self.assertEqual({person["assignment"].employee.name: person["outcome"]
                          for person in chart["all_rows"][0]["people"]},
                         {"Alice": "passed", "Bob": "retry_needed", "Carol": "not_attempted"})
        self.assertContains(response, "View chart values")
        self.assertContains(response, "Assignments by employee")
        self.assertContains(response, "Retry needed")
        self.assertNotContains(response, "Source: recorded quiz attempts")
        self.assertNotContains(response, "quiz assignment cycles")
        self.assertContains(response, "Alice")
        self.assertContains(response, "Bob")
        self.assertContains(response, "Carol")
        self.assertEqual({row["assignment"].employee.name for row in response.context["rows"]},
                         {"Alice", "Bob", "Carol"})
        self.assertIn("mode=quiz", response.context["browse"].return_to())

        html = render_to_string("training/_quiz_outcomes_chart.html", {
            "quiz_chart": chart, "browse": response.context["browse"], "org_time_zone": "UTC",
        })
        svg = html[html.index("<svg"):html.index("</svg>") + 6]
        ElementTree.fromstring(svg)
        self.assertEqual(sum(segment["value"] for segment in chart["rows"][0]["segments"]), 3)
        self.assertEqual(sum(int(segment["text"].rstrip("%"))
                             for segment in chart["rows"][0]["segments"]), 100)

    def test_filters_and_full_dataset_apply_to_chart_and_employee_table(self):
        for index in range(27):
            assignment = self.assignment(f"Learner{index:02}")
            if index < 4:
                self.attempt(assignment, passed=False, minutes=index)
        other = Training.objects.create()
        other_version = TrainingVersion.objects.create(
            training=other, version_number=1, title="Privacy",
        )
        Quiz.objects.create(version=other_version, passing_percent=80)
        TrainingVersion.objects.filter(pk=other_version.pk).update(status=TrainingVersion.Status.PUBLISHED)
        other_version.refresh_from_db()
        self.assignment("Other", version=other_version)

        response = self.client.get(reverse("assignment_dashboard"),
                                   {"mode": "quiz", "training": str(self.training.id), "page": 2})
        chart = response.context["quiz_chart"]

        self.assertEqual(response.context["browse"].total, 27)
        self.assertEqual(len(response.context["rows"]), 2)
        self.assertEqual(chart["total_assignments"], 27)
        self.assertEqual(chart["rows"][0]["parts"]["retry_needed"], 4)
        self.assertEqual(len(chart["all_rows"][0]["people"]), 27)
        self.assertContains(response, "Learner00")
        self.assertContains(response, "Learner26")
        self.assertNotContains(response, "Other</td>")
        self.assertIn("mode=quiz", response.context["browse"].previous_url)
        self.assertIn(f"training={self.training.id}", response.context["browse"].previous_url)

    def test_no_quiz_assignments_have_an_explicit_empty_state(self):
        slide_training = Training.objects.create()
        slide_version = TrainingVersion.objects.create(
            training=slide_training, version_number=1, title="Slide only",
            status=TrainingVersion.Status.PUBLISHED,
        )
        self.assignment("SlideOnly", version=slide_version)

        response = self.client.get(reverse("assignment_dashboard"), {"mode": "quiz"})

        self.assertIsNone(response.context["quiz_chart"])
        self.assertContains(response, "No quiz assignments match these filters.")

    def test_queryset_scope_excludes_waived_and_preserves_search(self):
        passed = self.assignment("Passed")
        self.attempt(passed, passed=True, minutes=1)
        passed.completed_at = self.now
        passed.save()
        waived = self.assignment("Waived")
        waived.waived_at = self.now
        waived.save()
        inactive = self.assignment("Inactive", inactive=True)

        query = quiz_outcome_assignments(ReportFilters(state="pending", activity="all", q="Safety"),
                                         now=self.now)
        self.assertEqual(set(query.values_list("id", flat=True)), {passed.id, inactive.id})

    def test_repeat_cycles_are_separate_records_with_their_own_outcomes(self):
        first = self.assignment("Alice")
        self.attempt(first, passed=True, minutes=1)
        first.completed_at = self.now
        first.save()
        Assignment.objects.create(
            employee=first.employee, training=self.training, version=self.version,
            cycle_number=2, due_date=self.now.date(), due_timezone="UTC",
            due_at=self.now + timedelta(days=30), renews=first,
        )

        response = self.client.get(reverse("assignment_dashboard"), {"mode": "quiz"})
        chart = response.context["quiz_chart"]

        self.assertEqual(chart["total_assignments"], 2)
        self.assertEqual(chart["rows"][0]["parts"], {
            "passed": 1, "not_attempted": 1, "retry_needed": 0,
        })
        self.assertEqual({person["assignment"].cycle_number: person["outcome"]
                          for person in chart["all_rows"][0]["people"]},
                         {1: "passed", 2: "not_attempted"})

    def test_chart_plots_eight_trainings_and_values_include_all_nine(self):
        for index in range(9):
            training = Training.objects.create()
            version = TrainingVersion.objects.create(
                training=training, version_number=1, title=f"Course {index}",
            )
            Quiz.objects.create(version=version, passing_percent=75)
            TrainingVersion.objects.filter(pk=version.pk).update(status=TrainingVersion.Status.PUBLISHED)
            version.refresh_from_db()
            self.assignment(f"Learner{index}", version=version)

        response = self.client.get(reverse("assignment_dashboard"), {"mode": "quiz"})
        chart = response.context["quiz_chart"]

        self.assertEqual(chart["total_rows"], 9)
        self.assertEqual(len(chart["rows"]), 8)
        self.assertEqual(len(chart["all_rows"]), 9)
        self.assertEqual(chart["total_assignments"], 9)
        self.assertContains(response, "Showing 8 of 9 trainings")
        self.assertContains(response, "Course 8")
