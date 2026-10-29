import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignments import AssignmentInputError, Recipients, _fingerprint, create_assignments
from .groups import GroupInputError, create_group, group_members, set_group_members
from .models import Assignment, Employee, Slide, Training, TrainingVersion


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test",
                   ORG_TIME_ZONE="UTC")
class GroupTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(name="Admin", email="demo.admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.alice = Employee.objects.create(name="Alice", email="alice@example.test", is_demo=True)
        self.bob = Employee.objects.create(name="Bob", email="bob@example.test", is_demo=True)
        self.group = create_group(name="Team", actor=self.admin)
        self.training = Training.objects.create(is_demo=True)
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        Slide.objects.create(version=self.version, position=1, header="Read", body="Read")
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.save()
        self.due_date = (timezone.now() + timedelta(days=2)).date().isoformat()

    def test_group_names_are_unique_and_only_active_members_can_be_added(self):
        with self.assertRaises(GroupInputError):
            create_group(name=" team ", actor=self.admin)
        self.bob.is_active = False
        self.bob.save(update_fields=["is_active"])
        with self.assertRaises(GroupInputError):
            set_group_members(group_id=self.group.id, employee_ids=[self.bob.id], actor=self.admin)
        set_group_members(group_id=self.group.id, employee_ids=[self.alice.id], actor=self.admin)
        self.alice.is_active = False
        self.alice.save(update_fields=["is_active"])
        self.assertEqual([member.id for member in group_members(self.group)], [self.alice.id])
        self.assertEqual(self.group.changes.count(), 2)

    def test_submission_resolves_current_members_and_retry_preserves_results(self):
        set_group_members(group_id=self.group.id, employee_ids=[self.alice.id], actor=self.admin)
        operation_id = uuid.uuid4()
        first = create_assignments(operation_id=operation_id, actor=self.admin, version_id=self.version.id,
                                   recipients=Recipients.group(self.group.id), due_date_value=self.due_date)
        set_group_members(group_id=self.group.id, employee_ids=[self.bob.id], actor=self.admin)
        replay = create_assignments(operation_id=operation_id, actor=self.admin, version_id=self.version.id,
                                    recipients=Recipients.group(self.group.id), due_date_value=self.due_date)
        self.assertEqual([result.employee_id for result in first], [self.alice.id])
        self.assertEqual([result.employee_id for result in replay], [self.alice.id])
        self.assertEqual(Assignment.objects.count(), 1)
        later = create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                                   recipients=Recipients.group(self.group.id), due_date_value=self.due_date)
        self.assertEqual([result.employee_id for result in later], [self.bob.id])
        self.assertEqual(Assignment.objects.count(), 2)

    def test_legacy_employee_fingerprint_and_empty_group(self):
        old = _fingerprint(self.admin.id, self.version.id, timezone.now().date(), [self.alice.id])
        new = _fingerprint(self.admin.id, self.version.id, timezone.now().date(),
                           recipients=Recipients.employees([self.alice.id]))
        self.assertEqual(old, new)
        with self.assertRaisesMessage(AssignmentInputError, "This group has no members."):
            create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                               recipients=Recipients.group(self.group.id), due_date_value=self.due_date)

    def test_group_pages_require_an_administrator(self):
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        self.assertEqual(self.client.get(reverse("group_list")).status_code, 403)
        self.assertEqual(self.client.post(reverse("group_detail", args=[self.group.id]),
                                          {"action": "members"}).status_code, 403)

    def test_assign_page_reviews_and_submits_group_membership(self):
        set_group_members(group_id=self.group.id, employee_ids=[self.alice.id], actor=self.admin)
        url = reverse("assign_training", args=[self.training.id])
        data = {"operation_id": str(uuid.uuid4()), "version_id": str(self.version.id),
                "due_date": self.due_date, "recipient_source": "group", "group_id": str(self.group.id)}
        review = self.client.post(url, {**data, "action": "review"})
        self.assertContains(review, "Group:")
        self.assertContains(review, "Alice")
        result = self.client.post(url, {**data, "action": "submit"})
        self.assertContains(result, "1 created")
        self.assertEqual(Assignment.objects.get(employee=self.alice).version_id, self.version.id)
