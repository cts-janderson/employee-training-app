import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone as django_timezone

from .assignments import (
    AssignmentAccessError,
    AssignmentInputError,
    acknowledge_slide,
    assignment_state,
    create_assignments,
    deadline_status,
    due_date_boundary,
    learner_next_step,
    progress_status,
    validate_new_due_date,
)
from .employees import update_demo_employee
from .models import (
    Assignment,
    AssignmentOperation,
    AssignmentOperationResult,
    AssignmentSlideProgress,
    Employee,
    EmployeeProfileChange,
    Notification,
    Slide,
    Training,
    TrainingVersion,
)


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test", ORG_TIME_ZONE="America/New_York")
class AssignmentWorkflowTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(
            name="Demo Administrator", email="demo.admin@example.test",
            role=Employee.Role.ADMINISTRATOR, is_demo=True,
        )
        self.alice = Employee.objects.create(name="Alice", email="alice@example.test", is_demo=True)
        self.bob = Employee.objects.create(name="Bob", email="bob@example.test", is_demo=True)
        self.training = Training.objects.create()
        self.version = self.publish_version(1, "Safety")
        self.now = datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)

    def publish_version(self, number, title):
        version = TrainingVersion.objects.create(
            training=self.training, version_number=number, title=title,
        )
        Slide.objects.create(version=version, position=1, header="One", body="First slide")
        Slide.objects.create(version=version, position=2, header="Two", body="Second slide")
        version.status = TrainingVersion.Status.PUBLISHED
        version.published_at = django_timezone.now()
        version.save()
        return version

    def assign(self, employees=None, version=None, due_date="2026-10-27", operation_id=None, now=None):
        return create_assignments(
            operation_id=operation_id or uuid.uuid4(), actor=self.admin,
            version_id=(version or self.version).id,
            employee_ids=[employee.id for employee in (employees or [self.alice])],
            due_date_value=due_date, now=now or self.now,
        )

    def test_non_demo_administrator_cannot_create_assignments(self):
        self.admin.is_demo = False
        self.admin.save(update_fields=["is_demo"])

        with self.assertRaisesMessage(AssignmentAccessError, "Administrator access is required."):
            self.assign()

        self.assertFalse(Assignment.objects.exists())
        self.assertFalse(AssignmentOperation.objects.exists())

    def test_bulk_replay_keeps_original_results_after_completion_and_conflict(self):
        operation_id = uuid.uuid4()
        first = self.assign([self.alice, self.bob], operation_id=operation_id)
        self.assertEqual({row.outcome for row in first}, {AssignmentOperationResult.Outcome.CREATED})
        self.assertEqual(Assignment.objects.count(), 2)
        alice_assignment = Assignment.objects.get(employee=self.alice)
        for slide in self.version.slides.all():
            acknowledge_slide(assignment_id=alice_assignment.id, slide_id=slide.id, employee=self.alice, now=self.now)
        revised = self.publish_version(2, "Revised Safety")
        conflict = self.assign([self.bob], version=revised, due_date="2026-10-28")
        self.assertEqual(conflict[0].outcome, AssignmentOperationResult.Outcome.CONFLICT)
        replay = self.assign(
            [self.alice, self.bob], operation_id=operation_id,
            now=self.now + timedelta(days=30),
        )
        self.assertEqual({row.id for row in replay}, {row.id for row in first})
        self.assertEqual(Assignment.objects.count(), 2)
        deliberate = self.assign([self.alice], version=revised, due_date="2026-10-28")
        self.assertEqual(deliberate[0].outcome, AssignmentOperationResult.Outcome.CREATED)
        self.assertEqual(Assignment.objects.count(), 3)
        self.assertEqual(Assignment.objects.get(pk=alice_assignment.id).version_id, self.version.id)

    def test_duplicate_assignment_cannot_create_second_pending_obligation(self):
        first = self.assign()[0].assignment
        same = self.assign()[0]
        self.assertEqual(same.outcome, AssignmentOperationResult.Outcome.ALREADY_ASSIGNED)
        self.assertEqual(same.assignment_id, first.id)
        revised = self.publish_version(2, "Revised Safety")
        different = self.assign(version=revised, due_date="2026-10-28")[0]
        self.assertEqual(different.outcome, AssignmentOperationResult.Outcome.CONFLICT)
        self.assertEqual(Assignment.objects.count(), 1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Assignment.objects.create(
                    employee=self.alice, training=self.training, version=revised,
                    due_date=date(2026, 10, 28), due_timezone="America/New_York",
                    due_at=due_date_boundary(date(2026, 10, 28), "America/New_York"),
                )

    def test_learner_navigation_acknowledgment_completion_and_ownership(self):
        self.assign([self.alice, self.bob])
        alice_assignment = Assignment.objects.get(employee=self.alice)
        bob_assignment = Assignment.objects.get(employee=self.bob)
        slides = list(self.version.slides.all())
        response = self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(self.client.get(reverse("home")), reverse("my_training"))
        self.assertContains(self.client.get(reverse("my_training")), "Safety")
        self.assertEqual(self.client.get(reverse("assignment_detail", args=[bob_assignment.id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("training_list")).status_code, 403)

        slide_url = reverse("assignment_slide", args=[alice_assignment.id, slides[1].id])
        self.assertEqual(self.client.get(slide_url).status_code, 200)
        alice_assignment.refresh_from_db()
        self.assertEqual(alice_assignment.resume_slide_id, slides[1].id)
        self.assertIsNotNone(alice_assignment.started_at)
        self.assertEqual(progress_status(alice_assignment), "in_progress")
        self.assertEqual(alice_assignment.slide_progress.count(), 0)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.admin.id)})
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        self.assertContains(self.client.get(reverse("assignment_detail", args=[alice_assignment.id])), "Resume at saved slide")
        first_ack = reverse("acknowledge", args=[alice_assignment.id, slides[0].id])
        self.assertEqual(self.client.post(first_ack).status_code, 302)
        alice_assignment.refresh_from_db()
        self.assertIsNone(alice_assignment.completed_at)
        acknowledged_at = AssignmentSlideProgress.objects.get(assignment=alice_assignment, slide=slides[0]).acknowledged_at
        self.assertEqual(self.client.post(first_ack).status_code, 302)
        self.assertEqual(AssignmentSlideProgress.objects.get(assignment=alice_assignment, slide=slides[0]).acknowledged_at, acknowledged_at)
        self.assertEqual(self.client.post(reverse("acknowledge", args=[alice_assignment.id, slides[1].id])).status_code, 302)
        alice_assignment.refresh_from_db()
        self.assertEqual(progress_status(alice_assignment), "completed")
        completed_at = alice_assignment.completed_at
        self.assertEqual(self.client.post(first_ack).status_code, 302)
        alice_assignment.refresh_from_db()
        self.assertEqual(alice_assignment.completed_at, completed_at)
        self.assertIsNone(bob_assignment.completed_at)

        revised = self.publish_version(2, "Other slides")
        foreign_slide = revised.slides.first()
        self.assertEqual(self.client.post(reverse("acknowledge", args=[alice_assignment.id, foreign_slide.id])).status_code, 403)
        self.assertEqual(self.client.get(reverse("assignment_slide", args=[alice_assignment.id, foreign_slide.id])).status_code, 403)

    def test_next_action_resumes_saved_place_then_finds_remaining_slide(self):
        assignment = self.assign()[0].assignment
        first, second = list(self.version.slides.all())
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        detail_url = reverse("assignment_detail", args=[assignment.id])
        first_url = reverse("assignment_slide", args=[assignment.id, first.id])
        second_url = reverse("assignment_slide", args=[assignment.id, second.id])
        initial = self.client.get(detail_url)
        self.assertContains(initial, "Next: Start slide 1")
        self.assertContains(initial, first_url)
        visited = self.client.get(second_url)
        self.assertContains(visited, "Slide 2 of 2")
        self.assertContains(visited, "2 acknowledgments remaining")
        self.assertEqual(assignment.slide_progress.count(), 0)
        resumed = self.client.get(detail_url)
        self.assertContains(resumed, "Resume at saved slide: Two")
        self.assertContains(resumed, second_url)
        second_ack = reverse("acknowledge", args=[assignment.id, second.id])
        self.assertRedirects(self.client.post(second_ack), first_url + "?slide_acknowledged=1")
        remaining = self.client.get(first_url)
        self.assertContains(remaining, "1 acknowledgment remaining")
        self.assertRedirects(self.client.post(second_ack), first_url + "?slide_acknowledged=1")
        self.assertEqual(assignment.slide_progress.count(), 1)
        first_ack = reverse("acknowledge", args=[assignment.id, first.id])
        self.assertRedirects(self.client.post(first_ack), detail_url + "?slide_acknowledged=1")
        assignment.refresh_from_db()
        self.assertIsNotNone(assignment.completed_at)
        completed = self.client.get(detail_url)
        self.assertContains(completed, "Training complete")
        self.assertContains(completed, "You acknowledged every slide")
        self.assertContains(self.client.get(second_url), "You've acknowledged this slide")

    def test_my_training_prioritizes_overdue_unfinished_and_preserves_late_completion(self):
        upcoming = self.assign(due_date="2026-10-30")[0].assignment
        overdue_training = Training.objects.create()
        overdue_version = TrainingVersion.objects.create(
            training=overdue_training, version_number=1, title="Overdue course",
        )
        overdue_slide = Slide.objects.create(version=overdue_version, position=1, header="Overdue slide", body="Read")
        overdue_version.status = TrainingVersion.Status.PUBLISHED
        overdue_version.save()
        overdue = Assignment.objects.create(
            employee=self.alice, training=overdue_training, version=overdue_version,
            due_date=date(2026, 10, 27), due_timezone="America/New_York",
            due_at=due_date_boundary(date(2026, 10, 27), "America/New_York"),
        )
        finished_training = Training.objects.create()
        finished_version = TrainingVersion.objects.create(
            training=finished_training, version_number=1, title="Finished course",
        )
        finished_slide = Slide.objects.create(version=finished_version, position=1, header="Finished slide", body="Read")
        finished_version.status = TrainingVersion.Status.PUBLISHED
        finished_version.save()
        finished = Assignment.objects.create(
            employee=self.alice, training=finished_training, version=finished_version,
            due_date=date(2026, 10, 26), due_timezone="America/New_York",
            due_at=due_date_boundary(date(2026, 10, 26), "America/New_York"),
        )
        acknowledge_slide(
            assignment_id=finished.id, slide_id=finished_slide.id, employee=self.alice,
            now=datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
        )
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        with patch("training.assignment_views.timezone.now", return_value=datetime(2026, 10, 28, 12, tzinfo=timezone.utc)):
            response = self.client.get(reverse("my_training"))
        html = response.content.decode()
        self.assertLess(html.index("Overdue course"), html.index("Safety"))
        self.assertLess(html.index("Safety"), html.index("Finished course"))
        self.assertContains(response, "Start")
        self.assertContains(response, "Overdue")
        self.assertContains(response, "Completed late")
        self.assertContains(response, reverse("assignment_slide", args=[overdue.id, overdue_slide.id]))
        detail = self.client.get(reverse("assignment_detail", args=[finished.id]))
        self.assertContains(detail, "Completed late")
        self.assertContains(detail, reverse("assignment_slide", args=[finished.id, finished_slide.id]))
        self.assertEqual(upcoming.version_id, self.version.id)

    def test_deadline_uses_full_local_day_and_daylight_saving_boundaries(self):
        self.assertEqual(
            due_date_boundary(date(2026, 3, 8), "America/New_York"),
            datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            due_date_boundary(date(2026, 11, 1), "America/New_York"),
            datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc),
        )
        boundary = due_date_boundary(date(2026, 10, 27), "America/New_York")
        assignment = SimpleNamespace(due_at=boundary, completed_at=None)
        self.assertEqual(deadline_status(assignment, now=boundary - timedelta(microseconds=1)), "on_time")
        self.assertEqual(deadline_status(assignment, now=boundary), "overdue")
        assignment.completed_at = boundary - timedelta(microseconds=1)
        self.assertEqual(deadline_status(assignment, now=boundary), "on_time")
        assignment.completed_at = boundary
        self.assertEqual(deadline_status(assignment, now=boundary), "completed_late")
        self.assertEqual(
            validate_new_due_date("2026-10-26", now=datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc))[0],
            date(2026, 10, 26),
        )
        with self.assertRaises(AssignmentInputError):
            validate_new_due_date("2026-10-25", now=self.now)

    def test_completion_uses_the_exact_deadline_boundary(self):
        self.assign([self.alice, self.bob])
        slides = list(self.version.slides.all())
        boundary = due_date_boundary(date(2026, 10, 27), "America/New_York")
        alice_assignment = Assignment.objects.get(employee=self.alice)
        bob_assignment = Assignment.objects.get(employee=self.bob)
        for slide in slides:
            acknowledge_slide(
                assignment_id=alice_assignment.id, slide_id=slide.id, employee=self.alice,
                now=boundary - timedelta(microseconds=1),
            )
            acknowledge_slide(
                assignment_id=bob_assignment.id, slide_id=slide.id, employee=self.bob,
                now=boundary if slide == slides[-1] else boundary - timedelta(microseconds=1),
            )
        alice_assignment.refresh_from_db()
        bob_assignment.refresh_from_db()
        self.assertEqual(deadline_status(alice_assignment, now=boundary), "on_time")
        self.assertEqual(deadline_status(bob_assignment, now=boundary), "completed_late")

    def test_deactivation_preserves_evidence_and_reactivation_restores_access(self):
        assignment = self.assign()[0].assignment
        slide = self.version.slides.first()
        acknowledge_slide(assignment_id=assignment.id, slide_id=slide.id, employee=self.alice, now=self.now)
        update_demo_employee(
            employee_id=self.alice.id, name="Alice Updated", email="alice.updated@example.test",
            active=False, actor=self.admin,
        )
        self.alice.refresh_from_db()
        self.assertEqual(EmployeeProfileChange.objects.filter(employee=self.alice).count(), 1)
        self.assertEqual(self.alice.assignments.count(), 1)
        self.assertEqual(assignment.slide_progress.count(), 1)
        self.assertEqual(self.assign()[0].outcome, AssignmentOperationResult.Outcome.INACTIVE)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        self.assertEqual(self.client.get(reverse("my_training")).status_code, 200)
        # An existing demo session loses access as soon as the employee becomes inactive.
        session = self.client.session
        session["demo_employee_id"] = str(self.alice.id)
        session.save()
        self.assertEqual(self.client.get(reverse("my_training")).status_code, 403)
        update_demo_employee(
            employee_id=self.alice.id, name="Alice Updated", email="alice.updated@example.test",
            active=True, actor=self.admin,
        )
        self.assertEqual(self.client.get(reverse("my_training")).status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.due_date, date(2026, 10, 27))
        self.assertEqual(assignment.slide_progress.count(), 1)
        self.assertEqual(Assignment.objects.count(), 1)

    def test_admin_can_bulk_assign_and_employee_can_open_their_version(self):
        today = django_timezone.now().astimezone(ZoneInfo("America/New_York")).date().isoformat()
        url = reverse("assign_training", args=[self.training.id])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {
            "operation_id": str(uuid.uuid4()),
            "version_id": str(self.version.id),
            "due_date": today,
            "employee_id": [str(self.alice.id), str(self.bob.id)],
        })
        self.assertContains(response, "Assignment results")
        self.assertEqual(Assignment.objects.count(), 2)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        assignment = Assignment.objects.get(employee=self.alice)
        self.assertContains(self.client.get(reverse("my_training")), "Safety")
        detail = self.client.get(reverse("assignment_detail", args=[assignment.id]))
        self.assertContains(detail, "Due")
        self.assertContains(detail, "0 of 2 slides acknowledged")

    def test_assignment_review_and_submit_keep_operation_id_and_report_each_result(self):
        url = reverse("assign_training", args=[self.training.id])
        due = (django_timezone.localdate() + timedelta(days=2)).isoformat()
        payload = {
            "operation_id": str(uuid.uuid4()), "version_id": str(self.version.id),
            "due_date": due, "employee_id": [str(self.alice.id), str(self.bob.id)],
        }
        review = self.client.post(url, {**payload, "action": "review"})
        self.assertContains(review, "Review assignment")
        self.assertContains(review, "Safety")
        self.assertContains(review, "Version 1")
        self.assertContains(review, "2 recipients")
        self.assertContains(review, due)
        self.assertContains(review, "Due")
        self.assertContains(review, "Recipient availability may change")
        self.assertContains(review, "Alice")
        self.assertContains(review, "Bob")
        self.assertEqual(Assignment.objects.count(), 0)
        result = self.client.post(url, {**payload, "action": "submit"})
        self.assertContains(result, "2 created")
        self.assertContains(result, "check notification status")
        self.assertEqual(Assignment.objects.count(), 2)
        self.assertEqual(Notification.objects.filter(kind=Notification.Kind.ASSIGNED).count(), 2)
        replay = self.client.post(url, {**payload, "action": "submit"})
        self.assertContains(replay, "2 created")
        self.assertEqual(Assignment.objects.count(), 2)
        self.assertEqual(Notification.objects.filter(kind=Notification.Kind.ASSIGNED).count(), 2)

    def test_assignment_review_warnings_are_provisional_and_final_result_is_authoritative(self):
        url = reverse("assign_training", args=[self.training.id])
        due = (django_timezone.localdate() + timedelta(days=3)).isoformat()
        existing_due = (django_timezone.localdate() + timedelta(days=1)).isoformat()
        self.assign([self.bob], due_date=existing_due)
        inactive = Employee.objects.create(name="Inactive", email="inactive@example.test", is_demo=True, is_active=False)
        payload = {
            "operation_id": str(uuid.uuid4()), "version_id": str(self.version.id),
            "due_date": due, "employee_id": [str(self.alice.id), str(self.bob.id), str(inactive.id)],
        }
        review = self.client.post(url, {**payload, "action": "review"})
        self.assertContains(review, "Ready to assign")
        self.assertContains(review, "Conflict — outstanding version")
        self.assertContains(review, "Inactive — no assignment")
        self.assign([self.alice], due_date=existing_due)
        submitted = self.client.post(url, {**payload, "action": "submit"})
        self.assertContains(submitted, "0 created")
        self.assertContains(submitted, "2 conflicts")
        self.assertContains(submitted, "1 inactive")
        self.assertEqual(Assignment.objects.count(), 2)

    def test_assignment_review_errors_keep_inputs_and_employee_cannot_access(self):
        url = reverse("assign_training", args=[self.training.id])
        payload = {
            "operation_id": str(uuid.uuid4()), "version_id": str(self.version.id),
            "due_date": "2020-01-01", "employee_id": [str(self.alice.id)], "action": "review",
        }
        invalid = self.client.post(url, payload)
        self.assertContains(invalid, "Choose today or a later due date", status_code=400)
        self.assertContains(invalid, 'value="2020-01-01"', status_code=400)
        self.assertContains(invalid, f'value="{self.alice.id}" checked', status_code=400)
        no_recipients = self.client.post(url, {**payload, "due_date": (django_timezone.localdate() + timedelta(days=1)).isoformat(), "employee_id": []})
        self.assertContains(no_recipients, "Choose one to 100 distinct employees", status_code=400)
        self.admin.role = Employee.Role.EMPLOYEE
        self.admin.save()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, payload).status_code, 403)

    def test_employee_forms_preserve_submitted_values_on_server_error(self):
        add = self.client.post(reverse("employee_directory"), {
            "name": "Entered Name", "email": "not-a-test@example.com",
        })
        self.assertContains(add, 'value="Entered Name"', status_code=400)
        self.assertContains(add, 'value="not-a-test@example.com"', status_code=400)
        self.assertContains(add, 'href="#employee-email"', status_code=400)
        duplicate = self.client.post(reverse("employee_directory"), {
            "name": "Another Alice", "email": self.alice.email,
        })
        self.assertContains(duplicate, 'value="Another Alice"', status_code=400)
        self.assertContains(duplicate, 'href="#employee-email"', status_code=400)
        edit = self.client.post(reverse("edit_employee", args=[self.alice.id]), {
            "name": "Typed New Name", "email": "wrong@example.com", "active": "on",
        })
        self.assertContains(edit, 'value="Typed New Name"', status_code=400)
        self.assertContains(edit, 'value="wrong@example.com"', status_code=400)
        self.assertContains(edit, 'href="#employee-email"', status_code=400)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.name, "Alice")

    def test_assignment_form_rejects_a_version_from_another_training(self):
        other_training = Training.objects.create()
        other_version = TrainingVersion.objects.create(
            training=other_training, version_number=1, title="Other training",
            status=TrainingVersion.Status.PUBLISHED,
            published_at=django_timezone.now(),
        )
        today = django_timezone.now().astimezone(ZoneInfo("America/New_York")).date().isoformat()
        response = self.client.post(reverse("assign_training", args=[self.training.id]), {
            "operation_id": str(uuid.uuid4()),
            "version_id": str(other_version.id),
            "due_date": today,
            "employee_id": [str(self.alice.id)],
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Assignment.objects.count(), 0)

    def test_directory_keeps_profile_history_for_same_employee(self):
        response = self.client.post(reverse("employee_directory"), {
            "name": "Carol", "email": "carol@example.test",
        })
        self.assertEqual(response.status_code, 302)
        carol = Employee.objects.get(email="carol@example.test")
        edit_url = reverse("edit_employee", args=[carol.id])
        self.assertEqual(self.client.post(edit_url, {
            "name": "Carol Updated", "email": "carol.updated@example.test", "active": "on",
        }).status_code, 302)
        carol.refresh_from_db()
        self.assertEqual(carol.name, "Carol Updated")
        self.assertEqual(carol.profile_changes.count(), 1)
        self.assertContains(self.client.get(edit_url), "carol@example.test")
        self.assertEqual(self.client.get(reverse("training_list")).status_code, 200)
        self.assertEqual(self.client.post(reverse("edit_employee", args=[self.admin.id]), {
            "name": "Demo Administrator", "email": "renamed.admin@example.test", "active": "on",
        }).status_code, 302)
        self.assertEqual(self.client.get(reverse("training_list")).status_code, 200)


class AssignmentLifecycleTests(TestCase):
    def test_outstanding_excludes_completed_and_canceled_assignments(self):
        training = Training.objects.create()
        version = TrainingVersion.objects.create(training=training, version_number=1)
        due_at = datetime(2026, 10, 28, tzinfo=timezone.utc)
        employees = [Employee.objects.create(name=f"Employee {index}", email=f"employee{index}@example.test")
                     for index in range(3)]
        assignments = [Assignment.objects.create(
            employee=employee, training=training, version=version,
            due_date=date(2026, 10, 27), due_timezone="America/New_York", due_at=due_at,
        ) for employee in employees]
        Assignment.objects.filter(pk=assignments[1].pk).update(completed_at=due_at)
        Assignment.objects.filter(pk=assignments[2].pk).update(canceled_at=due_at)

        self.assertEqual(list(Assignment.objects.outstanding().values_list("pk", flat=True)), [assignments[0].pk])

    def test_assignment_state_prioritizes_cancellation(self):
        completed_at = datetime(2026, 10, 27, tzinfo=timezone.utc)
        self.assertEqual(assignment_state(SimpleNamespace(completed_at=None, canceled_at=None)), "pending")
        self.assertEqual(assignment_state(SimpleNamespace(completed_at=completed_at, canceled_at=None)), "completed")
        self.assertEqual(assignment_state(SimpleNamespace(completed_at=None, canceled_at=completed_at)), "canceled")
        self.assertEqual(
            assignment_state(SimpleNamespace(completed_at=completed_at, canceled_at=completed_at)), "canceled",
        )

    def test_learner_next_step_uses_progress_and_saved_slide(self):
        first = SimpleNamespace(id=uuid.uuid4())
        second = SimpleNamespace(id=uuid.uuid4())
        slides = [first, second]
        assignment = SimpleNamespace(completed_at=None, started_at=None, resume_slide_id=None)
        self.assertEqual(learner_next_step(assignment, slides, set(), False), ("start", first))

        assignment.started_at = datetime(2026, 10, 27, tzinfo=timezone.utc)
        assignment.resume_slide_id = second.id
        self.assertEqual(learner_next_step(assignment, slides, set(), False), ("resume", second))
        self.assertEqual(learner_next_step(assignment, slides, {second.id}, False), ("resume", first))
        self.assertEqual(learner_next_step(assignment, slides, {first.id, second.id}, True), ("take_quiz", None))
        self.assertEqual(learner_next_step(assignment, slides, {first.id, second.id}, False), ("review", None))
        assignment.completed_at = assignment.started_at
        self.assertEqual(learner_next_step(assignment, slides, set(), True), ("review", None))
