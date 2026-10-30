import re
import threading
from datetime import timedelta
from unittest import mock, skipUnless
from urllib.parse import urlsplit

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .accounts import (AccountInputError, decide_application, invite_employee, prune_rate_limits, rate_limited,
                       register)
from .models import Assignment, Employee, RateLimitCounter, Slide, Training, TrainingVersion

PASSWORD = "correct horse battery"
NEW_PASSWORD = "another long passphrase"


def real_employee(email, *, role=Employee.Role.EMPLOYEE, password=PASSWORD, **fields):
    employee = Employee(name=email.split("@")[0].title(), email=email, verified_email=email,
                        email_verified_at=timezone.now(), role=role, **fields)
    employee.set_password(password)
    employee.save()
    return employee


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
class AccountTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.demo_admin = Employee.objects.create(name="Demo admin", email="demo.admin@example.test",
                                                  role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.admin = real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)

    def latest_link(self):
        return urlsplit(re.search(r"https?://\S+", mail.outbox[-1].body).group()).path

    def sign_in(self, email, password=PASSWORD, client=None):
        return (client or self.client).post(reverse("sign_in"), {"username": email, "password": password})

    def register(self, email="new@example.com", name="New Person", password=PASSWORD):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("register"), {"name": name, "email": email, "password": password})

    def set_password_through(self, path, password=NEW_PASSWORD, client=None):
        client = client or self.client
        form_url = client.get(path)["Location"]
        return client.post(form_url, {"new_password1": password, "new_password2": password})

    def test_registered_employee_needs_verification_and_approval_before_training(self):
        self.assertContains(self.register(), "Check your email")
        applicant = Employee.objects.get(email="new@example.com")
        self.assertFalse(applicant.is_demo)
        self.assertEqual(applicant.approval_status, Employee.Approval.PENDING)
        verify_path = self.latest_link()

        self.assertRedirects(self.sign_in("new@example.com"), reverse("home"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("my_training")), reverse("account_status"))
        self.assertContains(self.client.get(reverse("account_status")), "Confirm your email address")
        with self.assertRaisesMessage(AccountInputError, "hasn't verified"):
            decide_application(actor=self.admin, employee_id=applicant.pk, approve=True)

        self.assertContains(self.client.get(verify_path), "Confirm email address")
        self.assertContains(self.client.post(verify_path), "Email confirmed")
        self.assertRedirects(self.client.get(reverse("my_training")), reverse("account_status"))
        self.assertContains(self.client.get(reverse("account_status")), "waiting for an administrator")

        decide_application(actor=self.admin, employee_id=applicant.pk, approve=True)
        self.assertEqual(self.client.get(reverse("my_training")).status_code, 200)
        applicant.refresh_from_db()
        self.assertEqual(applicant.approval_decided_by, self.admin)
        self.assertEqual(applicant.role, Employee.Role.EMPLOYEE)

    def test_rejected_applicant_sees_rejection_and_cannot_reach_training(self):
        applicant = real_employee("applicant@example.com", approval_status=Employee.Approval.PENDING)
        decide_application(actor=self.admin, employee_id=applicant.pk, approve=False)
        self.sign_in("applicant@example.com")
        self.assertRedirects(self.client.get(reverse("my_training")), reverse("account_status"))
        self.assertContains(self.client.get(reverse("account_status")), "wasn't approved")
        with self.assertRaisesMessage(AccountInputError, "already been decided"):
            decide_application(actor=self.admin, employee_id=applicant.pk, approve=True)

    def test_registration_for_existing_email_changes_nothing_and_responds_the_same(self):
        existing = real_employee("taken@example.com")
        old_password, old_name = existing.password, existing.name
        response = self.register(email="TAKEN@example.com", name="Intruder", password=NEW_PASSWORD)
        self.assertContains(response, "If the address can be registered")
        existing.refresh_from_db()
        self.assertEqual((existing.password, existing.name), (old_password, old_name))
        self.assertEqual(Employee.objects.filter(email__iexact="taken@example.com").count(), 1)
        self.assertEqual(mail.outbox[-1].subject, "Someone tried to register with your email")

    def test_registration_rejects_weak_passwords_and_invalid_input(self):
        response = self.register(password="short")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Employee.objects.filter(email="new@example.com").exists())
        with self.assertRaisesMessage(AccountInputError, "valid email"):
            register(name="Someone", email="not-an-email", password=PASSWORD)

    def test_invited_employee_sets_password_and_keeps_id_and_assignments(self):
        self.sign_in("admin@example.com")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("account_admin"), {"action": "invite", "name": "Invited", "email": "invited@example.com"})
        invited = Employee.objects.get(email="invited@example.com")
        self.assertEqual(invited.approval_status, Employee.Approval.APPROVED)
        self.assertFalse(invited.has_usable_password())
        training = Training.objects.create(is_demo=True)
        version = TrainingVersion.objects.create(training=training, version_number=1, title="Safety")
        Slide.objects.create(version=version, position=1, header="Read", body="Read")
        version.status = TrainingVersion.Status.PUBLISHED
        version.save()
        now = timezone.now()
        assignment = Assignment.objects.create(employee=invited, training=training, version=version,
                                               due_date=now.date(), due_timezone="UTC", due_at=now + timedelta(days=3))
        setup_path = self.latest_link()

        other = self.client_class(HTTP_HOST="localhost")
        self.assertContains(self.set_password_through(setup_path, client=other), "Your account is set up")
        invited.refresh_from_db()
        self.assertTrue(invited.email_is_verified)
        self.sign_in("invited@example.com", NEW_PASSWORD, client=other)
        self.assertContains(other.get(reverse("my_training")), "Safety")
        assignment.refresh_from_db()
        self.assertEqual(assignment.employee_id, invited.pk)
        self.assertContains(other.get(setup_path), "invalid or has expired")

    def test_links_fail_for_wrong_purpose_modification_and_supersession(self):
        with self.captureOnCommitCallbacks(execute=True):
            invite_employee(actor=self.admin, name="Invited", email="invited@example.com")
        setup_path = self.latest_link()
        _, _, uidb64, token, _ = setup_path.split("/")[1:]
        wrong_purpose = reverse("password_reset_confirm", args=[uidb64, token])
        self.assertContains(self.client.get(wrong_purpose), "invalid or has expired")
        modified = reverse("account_setup", args=[uidb64, token[:-1] + ("a" if token[-1] != "a" else "b")])
        self.assertContains(self.client.get(modified), "invalid or has expired")

        self.sign_in("admin@example.com")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("account_admin"), {"action": "resend_setup",
                                                        "employee_id": Employee.objects.get(email="invited@example.com").pk})
        self.assertContains(self.client_class(HTTP_HOST="localhost").get(setup_path), "invalid or has expired")
        self.assertEqual(self.client_class(HTTP_HOST="localhost").get(self.latest_link()).status_code, 302)

    def test_demo_inactive_and_wrong_password_sign_ins_fail_alike(self):
        real_employee("inactive@example.com", is_active=False)
        demo = Employee.objects.get(email="demo.admin@example.test")
        demo.set_password(PASSWORD)
        demo.save()
        for email, password in (("inactive@example.com", PASSWORD), ("demo.admin@example.test", PASSWORD),
                                ("admin@example.com", "wrong password here"), ("nobody@example.com", PASSWORD)):
            response = self.sign_in(email, password)
            self.assertEqual(response.status_code, 200, email)
            self.assertContains(response, "Please enter a correct email and password")

    def test_deactivation_ends_session_without_falling_back_to_demo(self):
        employee = real_employee("worker@example.com")
        self.sign_in("worker@example.com")
        self.assertEqual(self.client.get(reverse("my_training")).status_code, 200)
        Employee.objects.filter(pk=employee.pk).update(is_active=False)
        response = self.client.get(reverse("my_training"))
        self.assertRedirects(response, f"{reverse('sign_in')}?next={reverse('my_training')}",
                             fetch_redirect_response=False)
        self.assertNotIn("demo_employee_id", self.client.session)

    def test_sign_in_replaces_demo_choice_and_blocks_demo_switch(self):
        self.client.get(reverse("my_training"))
        self.assertIn("demo_employee_id", self.client.session)
        real_employee("worker@example.com")
        self.sign_in("worker@example.com")
        self.assertNotIn("demo_employee_id", self.client.session)
        response = self.client.get(reverse("my_training"))
        self.assertEqual(response.wsgi_request.employee.email, "worker@example.com")
        self.assertEqual(self.client.get(reverse("demo_switch")).status_code, 403)
        self.client.post(reverse("sign_out"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_only_signed_in_real_administrators_manage_accounts(self):
        self.client.get(reverse("my_training"))  # demo administrator session
        self.assertEqual(self.client.get(reverse("account_admin")).status_code, 302)
        real_employee("worker@example.com")
        self.sign_in("worker@example.com")
        self.assertEqual(self.client.get(reverse("account_admin")).status_code, 403)
        applicant = real_employee("applicant@example.com", approval_status=Employee.Approval.PENDING)
        with self.assertRaisesMessage(AccountInputError, "Administrator access is unavailable"):
            decide_application(actor=self.demo_admin, employee_id=applicant.pk, approve=True)

        self.client.post(reverse("sign_out"))
        self.sign_in("admin@example.com")
        self.assertContains(self.client.get(reverse("account_admin")), "applicant@example.com")
        self.client.post(reverse("account_admin"), {"action": "approve", "employee_id": applicant.pk})
        applicant.refresh_from_db()
        self.assertEqual(applicant.approval_status, Employee.Approval.APPROVED)

    def test_password_reset_keeps_approval_and_ends_other_sessions(self):
        employee = real_employee("worker@example.com", approval_status=Employee.Approval.PENDING)
        other = self.client_class(HTTP_HOST="localhost")
        self.sign_in("worker@example.com", client=other)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("password_reset"), {"email": "WORKER@example.com"})
        self.assertContains(self.set_password_through(self.latest_link()), "Your new password is saved")
        employee.refresh_from_db()
        self.assertEqual(employee.approval_status, Employee.Approval.PENDING)
        self.assertTrue(employee.check_password(NEW_PASSWORD))
        self.assertRedirects(other.get(reverse("account_status")),
                             f"{reverse('sign_in')}?next={reverse('account_status')}", fetch_redirect_response=False)

    def test_password_reset_isnt_sent_to_demo_or_unknown_addresses(self):
        with self.captureOnCommitCallbacks(execute=True):
            for email in ("demo.admin@example.test", "nobody@example.com"):
                response = self.client.post(reverse("password_reset"), {"email": email})
                self.assertContains(response, "If an account uses that address")
        self.assertEqual(mail.outbox, [])

    @override_settings(ACCOUNT_RATE_LIMITS={"sign_in": (2, 60), "sign_in_client": (100, 60), "sign_in_email": (100, 60),
                                            "register": (1, 60), "account_email": (1, 60)})
    def test_repeated_sign_in_attempts_are_limited(self):
        for _ in range(2):
            self.assertEqual(self.sign_in("admin@example.com", "wrong password here").status_code, 200)
        self.assertEqual(self.sign_in("admin@example.com").status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(ACCOUNT_RATE_LIMITS={"sign_in": (100, 60), "sign_in_client": (100, 60), "sign_in_email": (2, 60),
                                            "register": (1, 60), "account_email": (1, 60)})
    def test_sign_in_attempts_for_one_email_are_limited_across_addresses(self):
        for address in ("10.0.0.1", "10.0.0.2"):
            self.client.post(reverse("sign_in"), {"username": "admin@example.com", "password": "wrong"},
                             REMOTE_ADDR=address)
        response = self.client.post(reverse("sign_in"), {"username": "admin@example.com", "password": PASSWORD},
                                    REMOTE_ADDR="10.0.0.3")
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(ACCOUNT_RATE_LIMITS={"sign_in": (100, 60), "sign_in_client": (2, 60), "sign_in_email": (100, 60),
                                            "register": (1, 60), "account_email": (1, 60)})
    def test_sign_in_attempts_from_one_address_are_limited_across_emails(self):
        for email in ("first@example.com", "second@example.com"):
            self.client.post(reverse("sign_in"), {"username": email, "password": PASSWORD})
        self.assertEqual(self.sign_in("admin@example.com").status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_create_administrator_runs_only_once(self):
        Employee.objects.filter(pk=self.admin.pk).update(role=Employee.Role.EMPLOYEE)
        with mock.patch("training.management.commands.create_administrator.getpass", return_value=PASSWORD):
            call_command("create_administrator", name="First Admin", email="First@Example.com")
            with self.assertRaisesMessage(CommandError, "already exists"):
                call_command("create_administrator", name="Second", email="second@example.com")
        first = Employee.objects.get(email="first@example.com")
        self.assertEqual(first.role, Employee.Role.ADMINISTRATOR)
        self.assertFalse(first.is_demo)
        self.assertTrue(self.sign_in("first@example.com").status_code == 302)

    def test_demo_employees_get_unusable_passwords(self):
        self.assertFalse(self.demo_admin.has_usable_password())


@override_settings(ACCOUNT_RATE_LIMITS={"sign_in": (2, 60), "register": (1, 60 * 60)})
class RateLimitTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(minute=0, second=0, microsecond=0)

    def test_limit_applies_within_a_window_and_resets_in_the_next(self):
        results = [rate_limited("sign_in", "10.0.0.1:a@example.com", now=self.now + timedelta(seconds=n))
                   for n in range(3)]
        self.assertEqual(results, [False, False, True])
        self.assertFalse(rate_limited("sign_in", "10.0.0.1:a@example.com", now=self.now + timedelta(seconds=60)))

    def test_scopes_and_keys_count_separately(self):
        for _ in range(2):
            rate_limited("sign_in", "a", now=self.now)
        self.assertFalse(rate_limited("sign_in", "b", now=self.now))
        self.assertFalse(rate_limited("register", "a", now=self.now))

    def test_counters_store_a_digest_not_the_key(self):
        rate_limited("sign_in", "10.0.0.1:a@example.com", now=self.now)
        digest = RateLimitCounter.objects.get().key_digest
        self.assertEqual(len(digest), 64)
        self.assertNotIn("example", digest)

    def test_pruning_removes_counters_only_after_the_longest_window_ends(self):
        rate_limited("sign_in", "a", now=self.now)
        self.assertEqual(prune_rate_limits(now=self.now + timedelta(minutes=59)), 0)
        self.assertEqual(prune_rate_limits(now=self.now + timedelta(hours=1)), 1)
        self.assertFalse(RateLimitCounter.objects.exists())


@skipUnless(connection.vendor == "postgresql", "PostgreSQL upsert under concurrency")
@override_settings(ACCOUNT_RATE_LIMITS={"sign_in": (5, 60)})
class ConcurrentRateLimitTests(TransactionTestCase):
    def test_parallel_requests_each_get_their_own_count(self):
        workers, now = 8, timezone.now()
        barrier = threading.Barrier(workers)
        results = []

        def run():
            close_old_connections()
            try:
                barrier.wait()
                results.append(rate_limited("sign_in", "10.0.0.1:race@example.com", now=now))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=run) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(RateLimitCounter.objects.get().count, workers)
        self.assertEqual(sorted(results), [False] * 5 + [True] * 3)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL uniqueness under concurrency")
class ConcurrentEnrollmentTests(TransactionTestCase):
    def test_simultaneous_registration_and_invitation_create_one_employee(self):
        admin = real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        barrier = threading.Barrier(2)
        errors = []

        def run(work):
            close_old_connections()
            try:
                barrier.wait()
                work()
            except AccountInputError as exc:
                errors.append(str(exc))
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=run, args=(lambda: register(name="Self", email="race@example.com", password=PASSWORD),)),
            threading.Thread(target=run, args=(lambda: invite_employee(actor=admin, name="Invited", email="RACE@example.com"),)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(Employee.objects.filter(email__iexact="race@example.com").count(), 1)
        self.assertIn(errors, ([], ["An employee already uses that email."]))


class AccountMigrationTests(TransactionTestCase):
    def test_existing_employees_become_approved_with_unusable_passwords(self):
        previous = ("training", "0015_trainingversion_content_format_outlineitem_and_more")
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        try:
            old = executor.loader.project_state([previous]).apps
            legacy = old.get_model("training", "Employee").objects.create(name="Legacy", email="legacy@example.test",
                                                                          is_demo=True)
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            migrated = Employee.objects.get(pk=legacy.pk)
            self.assertFalse(migrated.has_usable_password())
            self.assertEqual(migrated.approval_status, Employee.Approval.APPROVED)
        finally:
            restore = MigrationExecutor(connection)
            restore.migrate(restore.loader.graph.leaf_nodes())
