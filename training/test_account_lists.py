from datetime import timedelta
from urllib.parse import urlencode

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .accounts import pending_applications
from .models import Employee
from .test_account_wayfinding import unconfirmed_applicant
from .test_accounts import PASSWORD, real_employee


def applicants(count, *, prefix="applicant", confirmed=True):
    """Pending registrations created a minute apart, oldest first."""
    start = timezone.now() - timedelta(days=1)
    made = []
    for index in range(count):
        email = f"{prefix}{index:02}@example.com"
        employee = (real_employee(email, approval_status=Employee.Approval.PENDING) if confirmed
                    else unconfirmed_applicant(email))
        Employee.objects.filter(pk=employee.pk).update(created_at=start + timedelta(minutes=index))
        made.append(employee)
    return made


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
class AccountListTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = real_employee("admin@example.com", role=Employee.Role.ADMINISTRATOR)
        Employee.objects.create(name="Demo admin", email="demo.admin@example.test",
                                role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.client.post(reverse("sign_in"), {"username": "admin@example.com", "password": PASSWORD})

    def list_url(self, **params):
        return f"{reverse('account_list')}?{urlencode(params)}"

    def act(self, return_to, **data):
        return self.client.post(f"{reverse('account_admin')}?{urlencode({'return_to': return_to})}", data,
                                follow=True)

    def test_accounts_previews_five_longest_waiting_and_links_to_the_rest(self):
        applicants(7)
        content = self.client.get(reverse("account_admin")).content.decode()
        self.assertIn("7 waiting", content)
        self.assertIn(f'href="{self.list_url(status="applications")}"', content.replace("&amp;", "&"))
        self.assertIn("See all 7", content)
        shown = [f"applicant{index:02}@example.com" for index in range(7) if f"applicant{index:02}@example.com" in content]
        self.assertEqual(shown, [f"applicant{index:02}@example.com" for index in range(5)])

    def test_no_see_all_link_when_everything_fits(self):
        applicants(5)
        response = self.client.get(reverse("account_admin"))
        self.assertContains(response, "5 waiting")
        self.assertNotContains(response, "See all")

    def test_recent_decisions_preview_ten(self):
        for employee in applicants(12, prefix="decided"):
            self.client.post(reverse("account_admin"), {"action": "approve", "employee_id": employee.pk})
        content = self.client.get(reverse("account_admin")).content.decode()
        self.assertIn("See all 12", content)
        self.assertEqual(content.count("· approved by Admin"), 10)

    def test_all_accounts_filters_by_status_and_searches(self):
        applicants(2)
        applicants(2, prefix="unconfirmed", confirmed=False)
        response = self.client.get(self.list_url(status="unconfirmed"))
        self.assertContains(response, "unconfirmed00@example.com")
        self.assertNotContains(response, "applicant00@example.com")
        self.assertContains(response, 'aria-current="page">Waiting for email confirmation')

        response = self.client.get(self.list_url(status="unconfirmed", q="unconfirmed01"))
        self.assertContains(response, "unconfirmed01@example.com")
        self.assertNotContains(response, "unconfirmed00@example.com")

    def test_removing_the_only_row_on_the_last_page_lands_on_the_new_last_page(self):
        made = applicants(26, prefix="unconfirmed", confirmed=False)
        page_two = self.list_url(status="unconfirmed", q="", page=2)
        self.assertContains(self.client.get(page_two), "unconfirmed25@example.com")
        response = self.act(page_two, action="remove", employee_id=made[-1].pk)
        self.assertContains(response, "Registration removed.")
        self.assertContains(response, "unconfirmed00@example.com")
        self.assertEqual(response.context["browse"].page.number, 1)

    def test_an_error_returns_to_the_same_filter_and_page(self):
        confirmed = real_employee("confirmed@example.com", approval_status=Employee.Approval.PENDING)
        unconfirmed_page = self.list_url(status="unconfirmed", q="")
        response = self.act(unconfirmed_page, action="remove", employee_id=confirmed.pk)
        self.assertContains(response, "already been confirmed or removed")
        self.assertEqual(response.context["status"], "unconfirmed")

    def test_return_links_to_other_pages_or_hosts_are_ignored(self):
        applicant = applicants(1)[0]
        for target in ("https://evil.example/accounts/all/", reverse("employee_directory")):
            response = self.client.post(f"{reverse('account_admin')}?{urlencode({'return_to': target})}",
                                        {"action": "reject", "employee_id": applicant.pk})
            self.assertRedirects(response, reverse("account_list"), fetch_redirect_response=False)

    def test_only_real_administrators_open_all_accounts(self):
        self.client.post(reverse("sign_out"))
        self.client.get(reverse("my_training"))  # demo administrator session
        self.assertEqual(self.client.get(reverse("account_list")).status_code, 302)
        real_employee("worker@example.com")
        self.client.post(reverse("sign_in"), {"username": "worker@example.com", "password": PASSWORD})
        self.assertEqual(self.client.get(reverse("account_list")).status_code, 403)

    def test_verified_matching_ignores_case_like_the_model(self):
        mixed = real_employee("mixed@example.com", approval_status=Employee.Approval.PENDING)
        Employee.objects.filter(pk=mixed.pk).update(email="Mixed@Example.com", verified_email="mixed@EXAMPLE.com")
        accented = real_employee("zoe@example.com", approval_status=Employee.Approval.PENDING)
        Employee.objects.filter(pk=accented.pk).update(email="zoë@example.com", verified_email="zoë@example.com")
        unconfirmed = unconfirmed_applicant("unconfirmed@example.com")
        for employee in (mixed, accented, unconfirmed):
            employee.refresh_from_db()
            self.assertEqual(pending_applications().filter(pk=employee.pk).exists(), employee.email_is_verified)
