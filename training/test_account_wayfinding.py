from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AddressBlock, Employee
from .sign_in_protection import lift_block
from .test_accounts import PASSWORD, real_employee


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
