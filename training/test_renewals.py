import uuid
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Event
from unittest import skipUnless

from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone as django_timezone

from .assignments import AssignmentInputError, Recipients, _fingerprint, archive_assignment, create_assignments
from .models import Assignment, Employee, Slide, Training, TrainingVersion
from .renewals import renewal_candidates, renewal_date


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="admin@example.test", ORG_TIME_ZONE="UTC")
class RenewalTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(name="Admin", email="admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.employee = Employee.objects.create(name="Alice", email="alice@example.test", is_demo=True)
        self.training = Training.objects.create(renewal_interval_months=1)
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        Slide.objects.create(version=self.version, position=1, header="Read", body="Read")
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.save()
        self.now = django_timezone.now()
        self.due = (self.now + timedelta(days=2)).date().isoformat()

    def completed(self):
        assignment = Assignment.objects.create(
            employee=self.employee, training=self.training, version=self.version,
            due_date=self.now.date(), due_timezone="UTC", due_at=self.now + timedelta(days=1),
            completed_at=self.now - timedelta(days=40),
        )
        return assignment

    def test_renewal_date_clamps_month_end(self):
        assignment = self.completed()
        assignment.completed_at = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)
        self.assertEqual(renewal_date(assignment), date(2026, 2, 28))

    def test_renewal_creates_linked_cycle_and_replay_keeps_result(self):
        previous = self.completed()
        candidates = renewal_candidates(self.training, as_of=self.now.date())
        self.assertEqual([item.completed_assignment.id for item in candidates], [previous.id])
        operation = uuid.uuid4()
        recipients = Recipients.renewal(self.training.id, self.now.date())
        first = create_assignments(operation_id=operation, actor=self.admin, version_id=self.version.id,
                                   recipients=recipients, due_date_value=self.due, now=self.now)
        renewed = first[0].assignment
        self.assertEqual(renewed.renews_id, previous.id)
        self.assertEqual(renewed.cycle_number, 2)
        self.assertTrue(renewed.notifications.filter(kind="assigned").exists())
        self.assertEqual(renewal_candidates(self.training, as_of=self.now.date()), [])
        replay = create_assignments(operation_id=operation, actor=self.admin, version_id=self.version.id,
                                    recipients=recipients, due_date_value=self.due, now=self.now)
        self.assertEqual(replay[0].assignment_id, renewed.id)
        previous.refresh_from_db()
        self.assertIsNotNone(previous.completed_at)

    def test_renewal_page_reviews_and_creates_cycle(self):
        self.completed()
        url = reverse("renew_training", args=[self.training.id])
        data = {"operation_id": str(uuid.uuid4()), "version_id": str(self.version.id),
                "as_of": self.now.date().isoformat(), "due_date": self.due}
        self.assertContains(self.client.post(url, {**data, "action": "review"}), "Renewal date")
        self.assertContains(self.client.post(url, {**data, "action": "submit"}), "Renewal results")
        self.assertEqual(Assignment.objects.filter(employee=self.employee).count(), 2)

    def test_source_fingerprints_match_original_payloads(self):
        operation_date = date(2026, 10, 27)
        for recipients, parts in (
            (Recipients.group(uuid.uuid4()), ["group", None]),
            (Recipients.renewal(self.training.id, operation_date),
             ["renewal", str(self.training.id), operation_date.isoformat()]),
        ):
            if recipients.source == "group":
                parts[1] = str(recipients.group_id)
            payload = [str(self.admin.id), str(self.version.id), operation_date.isoformat(), *parts]
            expected = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
            self.assertEqual(_fingerprint(self.admin.id, self.version.id, operation_date,
                                          recipients=recipients), expected)

    def test_error_precedence_for_group_and_renewal_sources(self):
        missing = Recipients.group(uuid.uuid4())
        with self.assertRaisesMessage(AssignmentInputError, "An assignment identifier is invalid."):
            create_assignments(operation_id="bad", actor=self.admin, version_id=self.version.id,
                               recipients=missing, due_date_value="bad")
        with self.assertRaisesMessage(AssignmentInputError, "Choose a valid due date."):
            create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                               recipients=missing, due_date_value="bad")
        other = Training.objects.create()
        unpublished = TrainingVersion.objects.create(training=other, version_number=1, title="Draft")
        with self.assertRaisesMessage(AssignmentInputError, "Choose a valid employee group."):
            create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=unpublished.id,
                               recipients=missing, due_date_value=self.due)
        self.completed()
        used_operation = uuid.uuid4()
        create_assignments(operation_id=used_operation, actor=self.admin, version_id=self.version.id,
                           recipients=Recipients.renewal(self.training.id, self.now.date()),
                           due_date_value=self.due, now=self.now)
        with self.assertRaisesMessage(AssignmentInputError, "Choose a version of the training being renewed."):
            create_assignments(operation_id=used_operation, actor=self.admin, version_id=self.version.id,
                               recipients=Recipients.renewal(other.id, self.now.date()),
                               due_date_value=self.due, now=self.now)
        from .assignment_views import _assignment_review
        with self.assertRaisesMessage(AssignmentInputError, "Choose today or a later due date"):
            _assignment_review(self.training, [self.version], str(self.version.id),
                               (self.now - timedelta(days=2)).date(), missing, uuid.uuid4())

    def test_renewal_rule_and_manual_assignment_conflict(self):
        completion = self.completed()
        self.assertEqual([item.completed_assignment.id for item in
                          renewal_candidates(self.training, as_of=self.now.date())], [completion.id])
        hand_due = (self.now + timedelta(days=4)).date().isoformat()
        hand = create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                                  employee_ids=[self.employee.id], due_date_value=hand_due, now=self.now)[0]
        self.assertEqual(hand.outcome, "created")
        candidates = renewal_candidates(self.training, as_of=self.now.date())
        self.assertEqual([item.completed_assignment.id for item in candidates], [completion.id])
        operation = uuid.uuid4()
        renewal = create_assignments(operation_id=operation, actor=self.admin, version_id=self.version.id,
                                     recipients=Recipients.renewal(self.training.id, self.now.date()),
                                     due_date_value=self.due, now=self.now)
        self.assertEqual([(item.outcome, item.assignment_id) for item in renewal], [("conflict", hand.assignment_id)])
        self.assertEqual(Assignment.objects.filter(employee=self.employee).count(), 2)
        review_url = reverse("renew_training", args=[self.training.id])
        response = self.client.post(review_url, {
            "action": "review", "operation_id": str(uuid.uuid4()), "version_id": str(self.version.id),
            "as_of": self.now.date().isoformat(), "due_date": self.due,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Conflict")
        self.assertEqual(response.context["review"]["rows"][0]["renews"].id, completion.id)

    def test_locked_candidates_are_limited_to_given_employees(self):
        first = self.completed()
        second_employee = Employee.objects.create(name="Bob", email="bob@example.test", is_demo=True)
        second = Assignment.objects.create(
            employee=second_employee, training=self.training, version=self.version,
            due_date=self.now.date(), due_timezone="UTC", due_at=self.now + timedelta(days=1),
            completed_at=self.now - timedelta(days=40),
        )
        with transaction.atomic():
            candidates = renewal_candidates(self.training, as_of=self.now.date(),
                                            employee_ids=[self.employee.id], lock=True)
        self.assertEqual([item.completed_assignment.id for item in candidates], [first.id])
        self.assertNotEqual(first.id, second.id)

    def test_renewal_under_way_is_not_reported_as_conflict(self):
        self.completed()
        create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                           recipients=Recipients.renewal(self.training.id, self.now.date()),
                           due_date_value=self.due, now=self.now)
        self.assertEqual(renewal_candidates(self.training, as_of=self.now.date()), [])

    def test_other_latest_endings_use_the_same_renewal_rule(self):
        expected_ids = set()
        for ending in ("canceled", "waived", "archived_completion", "completed_renewal"):
            employee = Employee.objects.create(name=ending, email=f"{ending}@example.test", is_demo=True)
            earlier = Assignment.objects.create(
                employee=employee, training=self.training, version=self.version,
                due_date=self.now.date(), due_timezone="UTC", due_at=self.now + timedelta(days=1),
                completed_at=self.now - timedelta(days=80),
            )
            later = Assignment.objects.create(
                employee=employee, training=self.training, version=self.version,
                renews=earlier if ending in {"canceled", "completed_renewal"} else None,
                due_date=self.now.date(), due_timezone="UTC", due_at=self.now + timedelta(days=1),
                canceled_at=self.now if ending == "canceled" else None,
                waived_at=self.now if ending == "waived" else None,
                completed_at=self.now - timedelta(days=40) if ending in {"archived_completion", "completed_renewal"} else None,
                archived_at=self.now if ending == "archived_completion" else None,
            )
            if ending == "canceled":
                expected_ids.add(earlier.id)
            elif ending == "completed_renewal":
                expected_ids.add(later.id)
        self.assertEqual({item.completed_assignment.id for item in
                          renewal_candidates(self.training, as_of=self.now.date())}, expected_ids)

    def test_renewal_submit_reads_assignments_made_after_review(self):
        alice = self.completed()
        bob = Employee.objects.create(name="Bob", email="bob@example.test", is_demo=True)
        Assignment.objects.create(
            employee=bob, training=self.training, version=self.version,
            due_date=self.now.date(), due_timezone="UTC", due_at=self.now + timedelta(days=1),
            completed_at=self.now - timedelta(days=40),
        )
        from .assignment_views import _assignment_review
        recipients = Recipients.renewal(self.training.id, self.now.date())
        before = _assignment_review(self.training, [self.version], str(self.version.id),
                                    self.due, recipients, uuid.uuid4())
        self.assertEqual({row["employee"].id for row in before["rows"]}, {self.employee.id, bob.id})
        create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                           employee_ids=[bob.id], due_date_value=(self.now + timedelta(days=4)).date(), now=self.now)
        results = create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                                     recipients=recipients, due_date_value=self.due, now=self.now)
        self.assertEqual({item.employee_id: item.outcome for item in results},
                         {self.employee.id: "created", bob.id: "conflict"})
        self.assertEqual(Assignment.objects.get(renews=alice).employee_id, self.employee.id)
        after = _assignment_review(self.training, [self.version], str(self.version.id),
                                   self.due, recipients, uuid.uuid4())
        self.assertEqual([(row["employee"].id, row["status"].startswith("Conflict"))
                          for row in after["rows"]], [(bob.id, True)])

    def test_renewal_submit_skips_completion_archived_after_review(self):
        completion = self.completed()
        from .assignment_views import _assignment_review
        recipients = Recipients.renewal(self.training.id, self.now.date())
        review = _assignment_review(self.training, [self.version], str(self.version.id),
                                    self.due, recipients, uuid.uuid4())
        self.assertEqual(review["count"], 1)
        Assignment.objects.filter(pk=completion.pk).update(archived_at=self.now)
        with self.assertRaisesMessage(AssignmentInputError, "No completed assignments are due for renewal"):
            create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                               recipients=recipients, due_date_value=self.due, now=self.now)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock behavior")
@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="admin@example.test", ORG_TIME_ZONE="UTC")
class ConcurrentRenewalTests(TransactionTestCase):
    def setUp(self):
        self.admin = Employee.objects.create(name="Admin", email="admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.employee = Employee.objects.create(name="Alice", email="alice@example.test", is_demo=True)
        self.training = Training.objects.create(renewal_interval_months=1)
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        Slide.objects.create(version=self.version, position=1, header="Read", body="Read")
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.save()
        self.now = django_timezone.now()
        self.completion = Assignment.objects.create(
            employee=self.employee, training=self.training, version=self.version,
            due_date=self.now.date(), due_timezone="UTC", due_at=self.now + timedelta(days=1),
            completed_at=self.now - timedelta(days=40),
        )

    def submit(self):
        close_old_connections()
        try:
            return create_assignments(
                operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                recipients=Recipients.renewal(self.training.id, self.now.date()),
                due_date_value=(self.now + timedelta(days=2)).date(), now=self.now,
            )
        finally:
            connections.close_all()

    def test_completion_archived_during_submission_is_not_renewed(self):
        archived = Event()

        def archive():
            close_old_connections()
            try:
                with transaction.atomic():
                    archive_assignment(assignment_id=self.completion.id, actor=self.admin,
                                       reason="Archived", now=self.now)
                    archived.set()
                    time.sleep(0.5)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(archive)
            self.assertTrue(archived.wait(5))
            second = pool.submit(self.submit)
            first.result(timeout=10)
            with self.assertRaisesMessage(AssignmentInputError, "No completed assignments are due for renewal"):
                second.result(timeout=10)
        self.assertFalse(Assignment.objects.filter(renews=self.completion).exists())

    def test_concurrent_renewals_of_one_completion_create_one_assignment(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.submit) for _ in range(2)]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result(timeout=10)[0].outcome)
                except AssignmentInputError as exc:
                    self.assertIn("No completed assignments are due for renewal", str(exc))
                    outcomes.append("no_candidate")
        self.assertEqual(sorted(outcomes), ["created", "no_candidate"])
        self.assertEqual(Assignment.objects.filter(renews=self.completion).count(), 1)
