import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AddressBlock, Employee
from .accounts import AccountInputError, resend_verification_for
from .sign_in_protection import lift_block
from .test_accounts import PASSWORD, limits, real_employee


def unconfirmed_applicant(email):
    applicant = real_employee(email, approval_status=Employee.Approval.PENDING)
    Employee.objects.filter(pk=applicant.pk).update(verified_email="", email_verified_at=None)
    return applicant


def open_block(network="198.51.100.1/32", *, hours=24):
    now = timezone.now()
    return AddressBlock.objects.create(network=network, failure_count=51, created_at=now,
                                       expires_at=now + timedelta(hours=hours))


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
class AccountWayfindingTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.demo_admin = Employee.objects.create(name="Demo admin", email="demo.admin@example.test",
                                                  role=Employee.Role.ADMINISTRATOR, is_demo=True)

    def sign_in(self, email):
        return self.client.post(reverse("sign_in"), {"username": email, "password": PASSWORD})

    def register(self, email="applicant@example.com"):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("register"), {"name": "Applicant", "email": email, "password": PASSWORD})

    # Real administrators

    def test_real_administrator_sees_waiting_applications_in_the_sidebar(self):
        real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        real_employee("first@example.com", approval_status=Employee.Approval.PENDING)
        real_employee("second@example.com", approval_status=Employee.Approval.PENDING)
        self.sign_in("admin@example.com")
        response = self.client.get(reverse("my_training"))
        self.assertContains(response, '<span class="nav-count">2<span class="visually-hidden"> applications waiting')
        self.assertNotContains(response, "Blocked addresses")

    def test_blocked_addresses_appears_in_the_sidebar_only_while_a_block_is_open(self):
        real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        self.sign_in("admin@example.com")
        block = open_block()
        response = self.client.get(reverse("address_blocks"))
        self.assertContains(response, 'class="nav-sub" href="/accounts/blocks/" aria-current="page"')
        self.assertContains(response, "1<span class=\"visually-hidden\"> network blocked")

        lift_block(block.network)
        response = self.client.get(reverse("address_blocks"))
        self.assertNotContains(response, 'class="nav-sub"')
        self.assertContains(response, 'href="/accounts/" aria-current="page"')

    def test_an_expired_block_isnt_counted(self):
        real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        self.sign_in("admin@example.com")
        open_block(hours=-1)
        self.assertNotContains(self.client.get(reverse("account_admin")), 'class="nav-sub"')

    def test_employees_see_no_account_links_or_counts(self):
        real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        real_employee("worker@example.com")
        real_employee("waiting@example.com", approval_status=Employee.Approval.PENDING)
        open_block()
        for email in ("worker@example.com", "waiting@example.com"):
            self.sign_in(email)
            response = self.client.get(reverse("account_status"))
            self.assertNotContains(response, "nav-count")
            self.assertNotContains(response, 'href="/accounts/')
            self.client.post(reverse("sign_out"))
        self.assertNotContains(self.client.get(reverse("sign_in")), "nav-count")

    # Demo administrators

    def test_demo_administrator_is_told_how_to_reach_real_accounts(self):
        self.client.get(reverse("my_training"))  # demo administrator session
        response = self.client.get(reverse("my_training"))
        self.assertContains(response, f'href="{reverse("demo_accounts")}"')
        response = self.client.get(reverse("demo_accounts"))
        self.assertContains(response, "You're using a demo profile")
        self.assertContains(response, "manage.py create_administrator")

        real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        real_employee("applicant@example.com", approval_status=Employee.Approval.PENDING)
        response = self.client.get(reverse("demo_accounts"))
        self.assertNotContains(response, "manage.py create_administrator")
        self.assertNotContains(response, "applicant@example.com")
        self.assertNotContains(response, "nav-count")

    def test_real_administrator_is_sent_from_the_demo_page_to_accounts(self):
        real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        self.sign_in("admin@example.com")
        self.assertRedirects(self.client.get(reverse("demo_accounts")), reverse("account_admin"))

    def test_demo_employee_cant_open_the_demo_accounts_page(self):
        worker = Employee.objects.create(name="Demo worker", email="worker@example.test", is_demo=True)
        self.client.post(reverse("demo_switch"), {"employee_id": worker.pk})
        self.assertEqual(self.client.get(reverse("demo_accounts")).status_code, 403)

    # Sign-in hint

    def test_sign_in_shows_the_first_administrator_command_until_one_exists(self):
        self.assertContains(self.client.get(reverse("sign_in")), "No administrator account exists yet")
        real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        self.assertNotContains(self.client.get(reverse("sign_in")), "No administrator account exists yet")

    @override_settings(DEV_AUTH_STUB=False)
    def test_sign_in_never_shows_the_command_without_the_stub(self):
        self.assertNotContains(self.client.get(reverse("sign_in")), "No administrator account exists yet")

    # Local outbox

    def test_local_outbox_lists_account_links_newest_first(self):
        self.register("first@example.com")
        self.register("second@example.com")
        response = self.client.get(reverse("local_outbox"))
        content = response.content.decode()
        self.assertIn("/account/verify/", content)
        self.assertLess(content.index("To second@example.com"), content.index("To first@example.com"))

    def test_local_outbox_is_refused_off_loopback_and_without_the_stub(self):
        self.assertEqual(self.client.get(reverse("local_outbox"), REMOTE_ADDR="203.0.113.9").status_code, 403)
        with self.settings(DEV_AUTH_STUB=False):
            self.assertEqual(self.client.get(reverse("local_outbox")).status_code, 403)
            self.assertNotContains(self.client.get(reverse("sign_in")), "Local outbox")

    # Unconfirmed registrations

    def test_unconfirmed_registration_waits_for_email_confirmation_not_approval(self):
        real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        self.register()
        self.sign_in("admin@example.com")
        response = self.client.get(reverse("account_admin"))
        content = response.content.decode()
        confirmation = content.index("Waiting for email confirmation")
        self.assertGreater(content.index("applicant@example.com"), confirmation)
        self.assertNotIn('value="approve"', content)
        self.assertNotContains(response, "nav-count")

    def test_administrator_resends_a_confirmation_link_that_confirms_the_email(self):
        real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        self.register()
        applicant = Employee.objects.get(email="applicant@example.com")
        self.sign_in("admin@example.com")
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("account_admin"), {"action": "resend_verification",
                                                                   "employee_id": applicant.pk}, follow=True)
        self.assertContains(response, "Confirmation link sent to applicant@example.com.")
        self.assertEqual([message.to for message in mail.outbox], [["applicant@example.com"]])

        path = urlsplit(re.search(r"https?://\S+", mail.outbox[-1].body).group()).path
        self.client.post(path)
        response = self.client.get(reverse("account_admin"))
        self.assertContains(response, 'value="approve"')
        self.assertContains(response, "Every registration has confirmed its email address.")

    @override_settings(ACCOUNT_RATE_LIMITS=limits(account_email=1))
    def test_confirmation_resends_share_the_applicants_limit(self):
        admin = real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        applicant = unconfirmed_applicant("applicant@example.com")
        resend_verification_for(actor=admin, employee_id=applicant.pk)
        with self.assertRaisesMessage(AccountInputError, "sent to this applicant recently"):
            resend_verification_for(actor=admin, employee_id=applicant.pk)

    def test_only_real_administrators_resend_and_only_to_unconfirmed_applicants(self):
        admin = real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        confirmed = real_employee("confirmed@example.com", approval_status=Employee.Approval.PENDING)
        unconfirmed = unconfirmed_applicant("unconfirmed@example.com")
        with self.assertRaisesMessage(AccountInputError, "Administrator access is unavailable"):
            resend_verification_for(actor=self.demo_admin, employee_id=unconfirmed.pk)
        with self.assertRaisesMessage(AccountInputError, "already confirmed"):
            resend_verification_for(actor=admin, employee_id=confirmed.pk)
        with self.assertRaisesMessage(AccountInputError, "already confirmed"):
            resend_verification_for(actor=admin, employee_id="not-an-id")
