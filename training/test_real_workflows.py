import threading
import uuid
from unittest import mock
from datetime import timedelta

from django.contrib.auth.forms import PasswordChangeForm
from django.core import mail
from django.db import close_old_connections, connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignments import AssignmentInputError, create_assignments
from .employees import EmployeeInputError, update_employee
from .groups import GroupInputError, create_group, set_group_members
from .models import Assignment, Employee, EmployeeGroup, Notification, Quiz, Slide, Training, TrainingVersion
from .notifications import _verified_recipient, retry_notification
from .test_accounts import PASSWORD, real_employee


def published_training(*, is_demo, title="Safety"):
    training = Training.objects.create(is_demo=is_demo)
    version = TrainingVersion.objects.create(training=training, version_number=1, title=title)
    Slide.objects.create(version=version, position=1, header="Read", body="Read")
    version.status = TrainingVersion.Status.PUBLISHED
    version.save()
    return training, version


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
class RealEmployeeWorkflowTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.demo_admin = Employee.objects.create(name="Demo admin", email="demo.admin@example.test",
                                                  role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.demo_employee = Employee.objects.create(name="Demo learner", email="learner@example.test", is_demo=True)
        self.admin = real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        self.worker = real_employee("worker@example.com")
        self.demo_training, self.demo_version = published_training(is_demo=True, title="Demo safety")
        self.training, self.version = published_training(is_demo=False, title="Real safety")
        self.now = timezone.now()
        self.due = (self.now + timedelta(days=3)).date().isoformat()

    def sign_in(self, email, client=None):
        return (client or self.client).post(reverse("sign_in"), {"username": email, "password": PASSWORD})

    def assign(self, version, employees, actor=None):
        return create_assignments(operation_id=uuid.uuid4(), actor=actor or self.admin, version_id=version.id,
                                  employee_ids=[employee.id for employee in employees], due_date_value=self.due,
                                  now=self.now)

    def test_real_administrator_works_in_a_separate_library(self):
        self.sign_in("admin@example.com")
        self.assertRedirects(self.client.get(reverse("home")), reverse("training_list"))
        listing = self.client.get(reverse("training_list"))
        self.assertContains(listing, "Real safety")
        self.assertNotContains(listing, "Demo safety")
        self.assertEqual(self.client.get(reverse("assign_training", args=[self.demo_training.id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("edit_employee", args=[self.demo_employee.id])).status_code, 404)
        self.client.post(reverse("new_training"))
        self.assertFalse(Training.objects.exclude(pk__in=[self.training.pk, self.demo_training.pk]).get().is_demo)

    def test_demo_administrator_cannot_reach_real_rows(self):
        self.assertContains(self.client.get(reverse("training_list")), "Demo safety")
        self.assertNotContains(self.client.get(reverse("training_list")), "Real safety")
        self.assertEqual(self.client.get(reverse("assign_training", args=[self.training.id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("edit_employee", args=[self.worker.id])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("employee_directory")), "worker@example.com")

    def test_services_refuse_targets_from_the_other_population(self):
        with self.assertRaises(AssignmentInputError):
            self.assign(self.demo_version, [self.demo_employee])
        with self.assertRaises(AssignmentInputError):
            self.assign(self.version, [self.demo_employee])
        group = create_group(name="Crew", actor=self.admin)
        self.assertFalse(group.is_demo)
        with self.assertRaises(GroupInputError):
            set_group_members(group_id=group.id, employee_ids=[self.demo_employee.id], actor=self.admin)
        with self.assertRaises(GroupInputError):
            set_group_members(group_id=group.id, employee_ids=[], actor=self.demo_admin)
        demo_assignment = self.assign(self.demo_version, [self.demo_employee], actor=self.demo_admin)[0].assignment
        notification = demo_assignment.notifications.first()
        with self.assertRaises(Notification.DoesNotExist):
            retry_notification(notification_id=notification.id, actor=self.admin)
        with self.assertRaises(EmployeeInputError):
            update_employee(employee_id=self.demo_employee.id, name="X", email="x@example.com", active=True,
                            actor=self.admin)

    def test_invited_employee_can_be_assigned_but_applicants_cannot(self):
        invited = Employee.objects.create(name="Invited", email="invited@example.com")
        self.assertEqual(self.assign(self.version, [invited])[0].outcome, "created")
        applicant = real_employee("applicant@example.com", approval_status=Employee.Approval.PENDING)
        with self.assertRaisesMessage(AssignmentInputError, "One or more employees were not found."):
            self.assign(self.version, [applicant])

    def test_signed_in_employee_sees_and_opens_only_their_own_assignment(self):
        mine = self.assign(self.version, [self.worker])[0].assignment
        other = self.assign(self.version, [real_employee("other@example.com")])[0].assignment
        self.sign_in("worker@example.com")
        self.assertContains(self.client.get(reverse("my_training")), "Real safety")
        self.assertEqual(self.client.get(reverse("assignment_detail", args=[mine.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("assignment_detail", args=[other.id])).status_code, 404)

    def test_unverified_recipient_waits_for_verification(self):
        invited = Employee.objects.create(name="Invited", email="invited@example.com")
        self.assertIsNone(_verified_recipient(invited))
        invited.verified_email = invited.email
        invited.save()
        self.assertEqual(_verified_recipient(invited), "invited@example.com")
        demo_elsewhere = Employee.objects.create(name="Odd", email="odd@example.com", verified_email="odd@example.com",
                                                 is_demo=True)
        self.assertIsNone(_verified_recipient(demo_elsewhere))

    def test_directory_shows_invitation_wording_and_setup_status(self):
        Employee.objects.create(name="Ivy Invited", email="ivy@example.com")
        self.sign_in("admin@example.com")
        directory = self.client.get(reverse("employee_directory"))
        self.assertContains(directory, "Send invitation")
        self.assertContains(directory, "Awaiting setup")
        self.client.post(reverse("employee_directory"), {"name": "New Hire", "email": "hire@example.com"})
        hire = Employee.objects.get(email="hire@example.com")
        self.assertFalse(hire.is_demo)
        self.assertFalse(hire.has_usable_password())
        update_employee(employee_id=self.worker.id, name="Worker", email="worker2@example.com", active=True,
                        actor=self.admin)
        self.assertContains(self.client.get(reverse("edit_employee", args=[self.worker.id])), "Email unverified")

    def test_demo_directory_keeps_demo_wording(self):
        directory = self.client.get(reverse("employee_directory"))
        self.assertContains(directory, "Add employee")
        self.assertNotContains(directory, "Send invitation")

    def test_email_change_needs_verification_and_keeps_setup_recoverable(self):
        invited = Employee.objects.create(name="Invited", email="invited@example.com",
                                          verified_email="invited@example.com")
        with self.captureOnCommitCallbacks(execute=True):
            update_employee(employee_id=self.worker.id, name="Worker", email="worker2@example.com", active=True,
                            actor=self.admin)
            update_employee(employee_id=invited.id, name="Invited", email="invited2@example.com", active=True,
                            actor=self.admin)
        self.worker.refresh_from_db()
        invited.refresh_from_db()
        self.assertFalse(self.worker.email_is_verified)
        self.assertIsNone(_verified_recipient(self.worker))
        subjects = {message.to[0]: message.subject for message in mail.outbox}
        self.assertEqual(subjects["worker2@example.com"], "Confirm your email address")
        self.assertEqual(subjects["invited2@example.com"], "Set up your training account")

    def test_deactivation_ends_sessions_even_after_reactivation(self):
        self.sign_in("worker@example.com")
        self.assertEqual(self.client.get(reverse("my_training")).status_code, 200)
        update_employee(employee_id=self.worker.id, name="Worker", email="worker@example.com", active=False,
                        actor=self.admin)
        update_employee(employee_id=self.worker.id, name="Worker", email="worker@example.com", active=True,
                        actor=self.admin)
        response = self.client.get(reverse("training_list"))
        self.assertRedirects(response, f"{reverse('sign_in')}?next={reverse('training_list')}",
                             fetch_redirect_response=False)
        # The dead real session must not have fallen back to the default demo administrator.
        self.assertRedirects(self.client.get(reverse("training_list")),
                             f"{reverse('sign_in')}?next={reverse('training_list')}", fetch_redirect_response=False)
        # Only an explicit demo profile choice leaves the ended real session behind.
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.demo_admin.id)})
        self.assertContains(self.client.get(reverse("training_list")), "Demo safety")

    def test_sessions_survive_secret_key_rotation(self):
        self.sign_in("worker@example.com")
        with override_settings(SECRET_KEY="rotated-" + "x" * 50,
                               SECRET_KEY_FALLBACKS=["development-only-change-before-deployment"]):
            self.assertEqual(self.client.get(reverse("my_training")).status_code, 200)

    def test_password_change_keeps_a_concurrent_email_change_and_deactivation(self):
        self.sign_in("worker@example.com")
        original_clean = PasswordChangeForm.clean_old_password

        def change_meanwhile(form):
            update_employee(employee_id=self.worker.id, name="Worker", email="worker2@example.com", active=False,
                            actor=self.admin)
            return original_clean(form)

        with mock.patch.object(PasswordChangeForm, "clean_old_password", change_meanwhile):
            response = self.client.post(reverse("password_change"), {
                "old_password": PASSWORD, "new_password1": "a different long phrase",
                "new_password2": "a different long phrase"})
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.email, "worker2@example.com")
        self.assertFalse(self.worker.is_active)
        self.assertTrue(self.worker.check_password(PASSWORD))
        self.assertTrue(response["Location"].startswith(reverse("sign_in")))

    def test_unicode_email_variants_share_one_sign_in_limit(self):
        for variant in ["worker@example.com", "ＷＯＲＫＥＲ@example.com", "ｗorker@example.com",
                        "worker@ｅxample.com", "Worker@Example.com"]:
            self.client.post(reverse("sign_in"), {"username": variant, "password": "wrong"})
        response = self.client.post(reverse("sign_in"), {"username": "ｗｏｒｋｅｒ@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 429)

    def test_last_administrator_who_can_sign_in_is_protected(self):
        helper = Employee.objects.create(name="Helper", email="helper@example.com", verified_email="helper@example.com",
                                         role=Employee.Role.ADMINISTRATOR)
        with self.assertRaisesMessage(EmployeeInputError, "Keep at least one administrator who can sign in."):
            update_employee(employee_id=self.admin.id, name="Admin", email="admin@example.com", active=False,
                            actor=helper)
        with self.assertRaisesMessage(EmployeeInputError, "Keep at least one administrator who can sign in."):
            update_employee(employee_id=self.admin.id, name="Admin", email="new@example.com", active=True,
                            actor=helper)


class ConcurrentDeactivationTests(TransactionTestCase):
    def test_two_administrators_cannot_deactivate_each_other(self):
        first = real_employee("first@example.com", role=Employee.Role.ADMINISTRATOR)
        second = real_employee("second@example.com", role=Employee.Role.ADMINISTRATOR)
        barrier = threading.Barrier(2)
        errors = []

        def run(actor, target):
            close_old_connections()
            try:
                barrier.wait()
                update_employee(employee_id=target.id, name=target.name, email=target.email, active=False,
                                actor=actor)
            except EmployeeInputError as exc:
                errors.append(str(exc))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=run, args=(first, second)),
                   threading.Thread(target=run, args=(second, first))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, ["Keep at least one administrator who can sign in."])
        self.assertEqual(Employee.objects.filter(role=Employee.Role.ADMINISTRATOR, is_active=True).count(), 1)


class PopulationMigrationTests(TransactionTestCase):
    def test_existing_library_rows_become_demo(self):
        previous = ("training", "0016_employee_accounts")
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        try:
            old = executor.loader.project_state([previous]).apps
            training = old.get_model("training", "Training").objects.create()
            quiz = old.get_model("training", "Quiz").objects.create(title="Library")
            group = old.get_model("training", "EmployeeGroup").objects.create(name="Crew", created_at=timezone.now())
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            self.assertTrue(Training.objects.get(pk=training.pk).is_demo)
            self.assertTrue(Quiz.objects.get(pk=quiz.pk).is_demo)
            self.assertTrue(EmployeeGroup.objects.get(pk=group.pk).is_demo)
            # Group names are unique per population, so a real group may reuse a demo name.
            EmployeeGroup.objects.create(name="crew", is_demo=False, created_at=timezone.now())
        finally:
            restore = MigrationExecutor(connection)
            restore.migrate(restore.loader.graph.leaf_nodes())
