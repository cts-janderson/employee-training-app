import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from .assignments import (
    AssignmentAccessError, AssignmentInputError, archive_assignment, cancel_assignment,
    create_assignments, end_assignment, waive_assignment,
)
from .models import Assignment, AssignmentHistory, Employee, Ending, Notification, Training, TrainingVersion


class AssignmentEndingTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(name="Alice", email="alice@example.test", is_demo=True)
        self.training = Training.objects.create()
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1,
                                                      status=TrainingVersion.Status.PUBLISHED)
        self.now = timezone.now()

    def assignment(self, **fields):
        return Assignment.objects.create(employee=self.employee, training=self.training, version=self.version,
                                         due_date=self.now.date(), due_timezone="UTC",
                                         due_at=self.now + timedelta(days=1), **fields)

    def test_ending_property_matches_query_annotation(self):
        pending = self.assignment()
        self.assertIsNone(Assignment.objects.with_ending().get(pk=pending.id).ending_value)
        pending.completed_at = self.now
        pending.save(update_fields=["completed_at"])
        self.assertEqual(pending.ending, Ending.COMPLETED)
        self.assertEqual(Assignment.objects.with_ending().get(pk=pending.id).ending_value, Ending.COMPLETED)
        self.employee = Employee.objects.create(name="Bob", email="bob@example.test", is_demo=True)
        canceled = self.assignment(canceled_at=self.now)
        self.assertEqual(canceled.ending, Ending.CANCELED)
        self.assertEqual(Assignment.objects.with_ending().get(pk=canceled.id).ending_value, Ending.CANCELED)

    def test_outstanding_matches_unique_constraint(self):
        first = self.assignment()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.assignment()
        first.completed_at = self.now
        first.save(update_fields=["completed_at"])
        self.assertFalse(Assignment.objects.outstanding().filter(pk=first.id).exists())
        second = self.assignment()
        self.assertTrue(Assignment.objects.outstanding().filter(pk=second.id).exists())

    def test_each_administrator_ending_records_the_same_history_shape(self):
        admin = Employee.objects.create(name="Admin", email="admin@example.test",
                                        role=Employee.Role.ADMINISTRATOR, is_demo=True)
        for ending, action, kind, suppression in (
            ("canceled", cancel_assignment, AssignmentHistory.Kind.CANCELED, "assignment_canceled"),
            ("waived", waive_assignment, AssignmentHistory.Kind.WAIVED, "assignment_waived"),
            ("archived", archive_assignment, AssignmentHistory.Kind.CANCELED, "assignment_canceled"),
        ):
            with self.subTest(ending=ending):
                self.employee = Employee.objects.create(name=ending, email=f"{ending}@example.test", is_demo=True)
                assignment = create_assignments(
                    operation_id=uuid.uuid4(), actor=admin, version_id=self.version.id,
                    employee_ids=[self.employee.id], due_date_value=self.now.date() + timedelta(days=4), now=self.now,
                )[0].assignment
                action(assignment_id=assignment.id, actor=admin, reason="  Reason  ", now=self.now)
                assignment.refresh_from_db()
                history = assignment.history.get(kind=kind)
                self.assertEqual((history.actor_id, history.occurred_at, history.reason),
                                 (admin.id, self.now, "Reason"))
                for field in ("due_date", "due_timezone", "due_at", "due_revision"):
                    self.assertEqual(getattr(history, f"old_{field}"), getattr(assignment, field))
                    self.assertEqual(getattr(history, f"new_{field}"), getattr(assignment, field))
                self.assertEqual(history.new_canceled_at, self.now if ending != "waived" else None)
                self.assertEqual(history.new_waived_at, self.now if ending == "waived" else None)
                for field in ("old_canceled_at", "old_waived_at", "old_archived_at", "new_archived_at"):
                    self.assertIsNone(getattr(history, field))
                reminder = assignment.notifications.get(kind=Notification.Kind.DUE_DAY_REMINDER)
                self.assertEqual((reminder.status, reminder.suppression_reason),
                                 (Notification.Status.SUPPRESSED, suppression))
                if ending == "archived":
                    self.assertEqual(set(assignment.history.values_list("kind", flat=True)),
                                     {AssignmentHistory.Kind.CANCELED, AssignmentHistory.Kind.ARCHIVED})

    def test_each_reason_check_keeps_its_message(self):
        admin = Employee.objects.create(name="Admin", email="admin@example.test",
                                        role=Employee.Role.ADMINISTRATOR, is_demo=True)
        assignment = self.assignment()
        for action, message in (
            (cancel_assignment, "Enter a cancellation reason of at most 1000 characters."),
            (waive_assignment, "Enter a waiver reason of at most 1000 characters."),
            (archive_assignment, "Enter an archive reason of at most 1000 characters."),
        ):
            for reason in (" ", "x" * 1001):
                with self.subTest(action=action.__name__, reason_length=len(reason)):
                    with self.assertRaisesMessage(AssignmentInputError, message):
                        action(assignment_id=assignment.id, actor=admin, reason=reason, now=self.now)
        self.assertFalse(assignment.history.exists())

    def test_self_waiver_of_ended_work_raises_access_error_before_conflict(self):
        admin = Employee.objects.create(name="Admin", email="admin@example.test",
                                        role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.employee = admin
        assignment = self.assignment(canceled_at=self.now)
        with self.assertRaisesMessage(AssignmentAccessError, "Another administrator must waive your own assignment."):
            waive_assignment(assignment_id=assignment.id, actor=admin, reason="Waive", now=self.now)

    def test_end_assignment_rejects_completion(self):
        admin = Employee.objects.create(name="Admin", email="admin@example.test",
                                        role=Employee.Role.ADMINISTRATOR, is_demo=True)
        assignment = self.assignment()
        with self.assertRaises(KeyError):
            end_assignment(assignment_id=assignment.id, ending=Ending.COMPLETED,
                           actor=admin, reason="Done", now=self.now)
        assignment.refresh_from_db()
        self.assertIsNone(assignment.completed_at)
        self.assertFalse(assignment.history.exists())
