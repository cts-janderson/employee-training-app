import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from io import StringIO
from unittest import mock, skipUnless

from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .account_views import PasswordChange
from .accounts import (AccountInputError, EmployeeBackend, _account_email_lock, expire_registrations, registration_digest,
                       remove_registration, request_password_reset, resend_verification_for, verify_email,
                       verification_tokens)
from .mail import AmbiguousMailError, TransientMailError
from .models import Employee, SecurityEvent
from .test_account_wayfinding import unconfirmed_applicant
from .test_accounts import PASSWORD, real_employee

DAY = timedelta(days=1)


def sent_at(employee, when):
    Employee.objects.filter(pk=employee.pk).update(verification_sent_at=when)


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
class RegistrationRemovalTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        self.demo_admin = Employee.objects.create(name="Demo admin", email="demo.admin@example.test",
                                                  role=Employee.Role.ADMINISTRATOR, is_demo=True)

    def sign_in(self, email, client=None):
        return (client or self.client).post(reverse("sign_in"), {"username": email, "password": PASSWORD})

    def test_administrator_removes_an_unconfirmed_registration_and_frees_its_email(self):
        applicant = unconfirmed_applicant("applicant@example.com")
        self.sign_in("admin@example.com")
        response = self.client.post(reverse("account_admin"), {"action": "remove", "employee_id": applicant.pk},
                                    follow=True)
        self.assertContains(response, "Registration removed.")
        self.assertFalse(Employee.objects.filter(pk=applicant.pk).exists())

        event = SecurityEvent.objects.get(kind=SecurityEvent.Kind.REGISTRATION_REMOVED)
        self.assertEqual((event.actor, event.employee), (self.admin, None))
        self.assertIn(str(applicant.pk), event.detail)
        self.assertIn(registration_digest("APPLICANT@example.com"), event.detail)
        self.assertNotIn("applicant@example.com", event.detail)

        self.client.post(reverse("sign_out"))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("register"), {"name": "Again", "email": "applicant@example.com",
                                                   "password": PASSWORD})
        self.assertEqual(Employee.objects.get(email="applicant@example.com").name, "Again")

    def test_only_unconfirmed_undecided_registrations_can_be_removed(self):
        confirmed = real_employee("confirmed@example.com", approval_status=Employee.Approval.PENDING)
        approved = unconfirmed_applicant("approved@example.com")
        Employee.objects.filter(pk=approved.pk).update(approval_status=Employee.Approval.APPROVED)
        rejected = unconfirmed_applicant("rejected@example.com")
        Employee.objects.filter(pk=rejected.pk).update(approval_status=Employee.Approval.REJECTED,
                                                       approval_decided_at=timezone.now(), approval_decided_by=self.admin)
        inactive = unconfirmed_applicant("inactive@example.com")
        Employee.objects.filter(pk=inactive.pk).update(is_active=False)
        demo = Employee.objects.create(name="Demo", email="demo@example.test", is_demo=True,
                                       approval_status=Employee.Approval.PENDING)
        for employee_id in (confirmed.pk, approved.pk, rejected.pk, inactive.pk, demo.pk, "not-an-id"):
            with self.assertRaisesMessage(AccountInputError, "already been confirmed or removed"):
                remove_registration(actor=self.admin, employee_id=employee_id)
        self.assertEqual(Employee.objects.filter(email__in=["confirmed@example.com", "approved@example.com",
                                                            "rejected@example.com", "inactive@example.com"]).count(), 4)

    def test_only_real_administrators_remove_registrations(self):
        applicant = unconfirmed_applicant("applicant@example.com")
        worker = real_employee("worker@example.com")
        for actor in (self.demo_admin, worker):
            with self.assertRaisesMessage(AccountInputError, "Administrator access is unavailable"):
                remove_registration(actor=actor, employee_id=applicant.pk)
        self.sign_in("worker@example.com")
        response = self.client.post(reverse("account_admin"), {"action": "remove", "employee_id": applicant.pk})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Employee.objects.filter(pk=applicant.pk).exists())

    def test_a_registration_other_records_refer_to_is_refused_cleanly(self):
        applicant = unconfirmed_applicant("applicant@example.com")
        SecurityEvent.objects.create(kind=SecurityEvent.Kind.PASSKEYS_COMMAND, employee=applicant,
                                     occurred_at=timezone.now())
        with self.assertRaisesMessage(AccountInputError, "Other records refer to this registration"):
            remove_registration(actor=self.admin, employee_id=applicant.pk)
        self.assertTrue(Employee.objects.filter(pk=applicant.pk).exists())
        self.assertFalse(SecurityEvent.objects.filter(kind=SecurityEvent.Kind.REGISTRATION_REMOVED).exists())

    def test_remove_passkeys_refuses_employees_who_arent_administrators(self):
        unconfirmed_applicant("applicant@example.com")
        with self.assertRaisesMessage(CommandError, "Only administrators have passkeys"):
            call_command("remove_passkeys", "--email", "applicant@example.com", stdout=StringIO())
        self.assertFalse(SecurityEvent.objects.exists())
        call_command("remove_passkeys", "--email", "admin@example.com", stdout=StringIO())
        self.assertTrue(SecurityEvent.objects.filter(kind=SecurityEvent.Kind.PASSKEYS_COMMAND).exists())

    def test_a_removed_applicants_next_request_is_signed_out(self):
        applicant = unconfirmed_applicant("applicant@example.com")
        browser = self.client_class(HTTP_HOST="localhost")
        self.sign_in("applicant@example.com", client=browser)
        self.assertEqual(browser.get(reverse("account_status")).status_code, 200)
        remove_registration(actor=self.admin, employee_id=applicant.pk)
        self.assertRedirects(browser.get(reverse("account_status")),
                             f"{reverse('sign_in')}?next={reverse('account_status')}", fetch_redirect_response=False)

    def test_a_password_change_in_flight_for_a_removed_applicant_signs_out(self):
        applicant = unconfirmed_applicant("applicant@example.com")
        loaded = Employee.objects.get(pk=applicant.pk)
        remove_registration(actor=self.admin, employee_id=applicant.pk)
        request = RequestFactory().post(reverse("password_change"), {
            "old_password": PASSWORD, "new_password1": "another long passphrase",
            "new_password2": "another long passphrase"}, HTTP_HOST="localhost")
        request._dont_enforce_csrf_checks = True
        SessionMiddleware(lambda request: None).process_request(request)
        request.user = loaded
        response = PasswordChange.as_view()(request)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("sign_in")))

    def test_a_sign_in_that_races_a_removal_doesnt_crash(self):
        applicant = unconfirmed_applicant("applicant@example.com")
        authenticate = EmployeeBackend.authenticate

        def removed_after_password_check(backend, *args, **kwargs):
            employee = authenticate(backend, *args, **kwargs)
            remove_registration(actor=self.admin, employee_id=applicant.pk)
            return employee

        with mock.patch.object(EmployeeBackend, "authenticate", removed_after_password_check):
            response = self.sign_in("applicant@example.com")
        self.assertRedirects(response, reverse("sign_in"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_any_spelling_of_the_id_waits_on_the_same_email_lock(self):
        applicant = unconfirmed_applicant("applicant@example.com")
        locked = []

        @contextmanager
        def recording_lock(employee_id):
            locked.append(employee_id)
            yield

        with mock.patch("training.accounts._account_email_lock", recording_lock):
            remove_registration(actor=self.admin, employee_id=str(applicant.pk).upper().replace("-", ""))
        self.assertEqual([str(key) for key in locked], [str(applicant.pk)])

    def test_a_confirmation_email_for_a_removed_registration_is_dropped(self):
        applicant = unconfirmed_applicant("applicant@example.com")
        with self.captureOnCommitCallbacks() as callbacks:
            resend_verification_for(actor=self.admin, employee_id=applicant.pk)
        Employee.objects.filter(pk=applicant.pk).delete()
        mail.outbox.clear()
        for callback in callbacks:
            callback()
        self.assertEqual(mail.outbox, [])


class RegistrationExpiryTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.applicant = unconfirmed_applicant("applicant@example.com")
        sent_at(self.applicant, self.now)

    def resend(self, **patch):
        admin = real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        with mock.patch("training.accounts.send_mail", **patch), self.captureOnCommitCallbacks(execute=True):
            resend_verification_for(actor=admin, employee_id=self.applicant.pk)

    def test_a_registration_expires_seven_days_after_its_confirmation_email(self):
        self.assertEqual(expire_registrations(now=self.now + 7 * DAY - timedelta(seconds=1)), 0)
        self.assertEqual(expire_registrations(now=self.now + 7 * DAY), 1)
        self.assertFalse(Employee.objects.filter(pk=self.applicant.pk).exists())
        event = SecurityEvent.objects.get()
        self.assertEqual((event.kind, event.actor), (SecurityEvent.Kind.REGISTRATION_EXPIRED, None))

    def test_a_registration_without_a_send_time_counts_from_registration(self):
        sent_at(self.applicant, None)
        Employee.objects.filter(pk=self.applicant.pk).update(created_at=self.now - 7 * DAY)
        self.assertEqual(expire_registrations(now=self.now), 1)

    def test_a_resend_restarts_the_count(self):
        sent_at(self.applicant, self.now - 6 * DAY)
        self.resend()
        self.assertEqual(expire_registrations(now=timezone.now() + 2 * DAY), 0)

    def test_a_resend_that_definitely_failed_doesnt_restart_the_count(self):
        sent_at(self.applicant, self.now - 6 * DAY)
        self.resend(side_effect=TransientMailError("graph_unreachable"))
        self.assertEqual(Employee.objects.get(pk=self.applicant.pk).verification_sent_at, self.now - 6 * DAY)
        self.assertEqual(expire_registrations(now=self.now + 2 * DAY), 1)

    def test_a_resend_that_may_have_arrived_restarts_the_count(self):
        sent_at(self.applicant, self.now - 6 * DAY)
        self.resend(side_effect=AmbiguousMailError("timeout"))
        self.assertEqual(expire_registrations(now=self.now + 2 * DAY), 0)

    def test_reset_emails_dont_restart_the_count(self):
        sent_at(self.applicant, self.now - 6 * DAY)
        with self.captureOnCommitCallbacks(execute=True):
            request_password_reset("applicant@example.com")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Employee.objects.get(pk=self.applicant.pk).verification_sent_at, self.now - 6 * DAY)

    def test_confirmed_applicants_and_invited_employees_never_expire(self):
        confirmed = real_employee("confirmed@example.com", approval_status=Employee.Approval.PENDING)
        invited = Employee(name="Invited", email="invited@example.com")
        invited.set_unusable_password()
        invited.save()
        Employee.objects.filter(pk__in=[confirmed.pk, invited.pk]).update(created_at=self.now - 30 * DAY)
        expire_registrations(now=self.now + 30 * DAY)
        self.assertEqual(Employee.objects.filter(pk__in=[confirmed.pk, invited.pk]).count(), 2)

    def test_protected_registrations_dont_hold_back_the_rest(self):
        newer = unconfirmed_applicant("newer@example.com")
        sent_at(newer, self.now + DAY)
        SecurityEvent.objects.create(kind=SecurityEvent.Kind.PASSKEYS_COMMAND, employee=self.applicant,
                                     occurred_at=self.now)
        self.assertEqual(expire_registrations(now=self.now + 8 * DAY, batch=1), 1)
        self.assertFalse(Employee.objects.filter(pk=newer.pk).exists())

    def test_a_registration_other_records_refer_to_is_kept_and_the_rest_expire(self):
        other = unconfirmed_applicant("other@example.com")
        sent_at(other, self.now)
        SecurityEvent.objects.create(kind=SecurityEvent.Kind.PASSKEYS_COMMAND, employee=self.applicant,
                                     occurred_at=self.now)
        self.assertEqual(expire_registrations(now=self.now + 7 * DAY), 1)
        self.assertTrue(Employee.objects.filter(pk=self.applicant.pk).exists())
        self.assertFalse(Employee.objects.filter(pk=other.pk).exists())


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row and advisory locks")
class ConcurrentRegistrationRemovalTests(TransactionTestCase):
    def setUp(self):
        self.admin = real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        self.applicant = unconfirmed_applicant("applicant@example.com")

    def run_together(self, *work):
        barrier = threading.Barrier(len(work))
        results = [None] * len(work)

        def run(index, job):
            close_old_connections()
            try:
                barrier.wait()
                results[index] = job()
            except AccountInputError as exc:
                results[index] = exc
            finally:
                connections.close_all()

        threads = [threading.Thread(target=run, args=(index, job)) for index, job in enumerate(work)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results

    def test_confirming_and_removing_at_once_does_exactly_one(self):
        token = verification_tokens.make_token(self.applicant)
        confirmed, removed = self.run_together(
            lambda: verify_email(self.applicant.pk, token),
            lambda: remove_registration(actor=self.admin, employee_id=self.applicant.pk),
        )
        exists = Employee.objects.filter(pk=self.applicant.pk).exists()
        if exists:
            self.assertIsNotNone(confirmed)
            self.assertIsInstance(removed, AccountInputError)
        else:
            self.assertIsNone(confirmed)
            self.assertIsNone(removed)

    def test_removal_waits_for_an_email_being_sent(self):
        held, finished = threading.Event(), []

        def sending():
            close_old_connections()
            try:
                with _account_email_lock(self.applicant.pk):
                    held.set()
                    time.sleep(0.3)
                    finished.append(("send", time.monotonic()))
            finally:
                connections.close_all()

        sender = threading.Thread(target=sending)
        sender.start()
        held.wait()
        remove_registration(actor=self.admin, employee_id=self.applicant.pk)
        finished.append(("remove", time.monotonic()))
        sender.join()
        self.assertEqual([name for name, _ in sorted(finished, key=lambda item: item[1])], ["send", "remove"])


class RegistrationExpiryMigrationTests(TransactionTestCase):
    def test_registrations_unconfirmed_at_upgrade_get_a_full_expiry_period(self):
        previous = ("training", "0020_sign_in_attack_protection")
        executor = MigrationExecutor(connection)
        executor.migrate([previous])
        try:
            old = executor.loader.project_state([previous]).apps.get_model("training", "Employee")
            legacy = old.objects.create(name="Legacy", email="legacy@example.com", approval_status="pending")
            old.objects.filter(pk=legacy.pk).update(created_at=timezone.now() - 30 * DAY)
            confirmed = old.objects.create(name="Confirmed", email="confirmed@example.com",
                                           verified_email="CONFIRMED@example.com", approval_status="pending")
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            self.assertGreater(Employee.objects.get(pk=legacy.pk).verification_sent_at, timezone.now() - DAY)
            self.assertIsNone(Employee.objects.get(pk=confirmed.pk).verification_sent_at)
            self.assertEqual(expire_registrations(), 0)
        finally:
            restore = MigrationExecutor(connection)
            restore.migrate(restore.loader.graph.leaf_nodes())
