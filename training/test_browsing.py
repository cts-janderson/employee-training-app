from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .browsing import browse, safe_return_to
from .models import Employee, Training, TrainingVersion


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
class BrowsingTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(name="Admin", email="demo.admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)

    def test_search_counts_all_matches_and_clamps_page(self):
        for index in range(30):
            Employee.objects.create(name=f"Search {index:02}", email=f"person{index}@example.test", is_demo=True)
        response = self.client.get(reverse("employee_directory"), {"q": "  Search   ", "page": "999"})
        listing = response.context["browse"]
        self.assertEqual(listing.total, 30)
        self.assertEqual(listing.page.number, 2)
        self.assertEqual(len(response.context["employees"]), 5)
        self.assertIn("q=Search", listing.previous_url)

    def test_search_does_not_list_non_demo_employees(self):
        Employee.objects.create(name="Hidden", email="hidden@example.test", is_demo=False)
        response = self.client.get(reverse("employee_directory"), {"q": "Hidden"})
        self.assertEqual(response.context["browse"].total, 0)

    def test_return_link_accepts_only_its_list_path(self):
        factory = RequestFactory()
        path = reverse("employee_directory")
        valid = factory.get(reverse("edit_employee", args=[self.admin.id]),
                            {"return_to": f"{path}?q=Admin&page=2"}, HTTP_HOST="localhost")
        self.assertEqual(safe_return_to(valid, "employee_directory"), f"{path}?q=Admin&page=2")
        for destination in ("https://evil.example/employees/", "/assignments/?q=Admin", "//evil.example/employees/"):
            invalid = factory.get(reverse("edit_employee", args=[self.admin.id]),
                                  {"return_to": destination}, HTTP_HOST="localhost")
            self.assertEqual(safe_return_to(invalid, "employee_directory"), path)

    def test_training_library_shows_both_states_and_keeps_published_title(self):
        training = Training.objects.create(is_demo=True)
        TrainingVersion.objects.create(training=training, version_number=1,
                                       status=TrainingVersion.Status.PUBLISHED,
                                       title="Safety basics", description="Published overview")
        TrainingVersion.objects.create(training=training, version_number=2,
                                       title="Updated safety basics")

        response = self.client.get(reverse("training_list"))

        self.assertEqual(response.context["browse"].total, 1)
        self.assertContains(response, "Safety basics")
        self.assertContains(response, "Draft title: Updated safety basics")
        self.assertContains(response, "Published v1")
        self.assertContains(response, "Draft v2")
        self.assertContains(response, "Edit draft")
        self.assertContains(response, "Assign training")

    def test_training_library_filters_overlap_and_survive_search_and_pagination(self):
        draft_only = Training.objects.create(is_demo=True)
        TrainingVersion.objects.create(training=draft_only, version_number=1, title="Draft only")
        published_only = Training.objects.create(is_demo=True)
        TrainingVersion.objects.create(training=published_only, version_number=1,
                                       status=TrainingVersion.Status.PUBLISHED, title="Published only")
        both = Training.objects.create(is_demo=True)
        TrainingVersion.objects.create(training=both, version_number=1,
                                       status=TrainingVersion.Status.PUBLISHED, title="Safety")
        TrainingVersion.objects.create(training=both, version_number=2, title="Aardvark update")
        url = reverse("training_list")

        all_rows = self.client.get(url).context["rows"]
        drafts = self.client.get(url, {"status": "draft"})
        published = self.client.get(url, {"status": "published"})
        searched = self.client.get(url, {"status": "published", "q": "update", "page": "99"})

        self.assertEqual([row["training"] for row in all_rows], [draft_only, published_only, both])
        self.assertEqual(drafts.context["browse"].total, 2)
        self.assertEqual(published.context["browse"].total, 2)
        self.assertEqual(searched.context["browse"].total, 1)
        self.assertEqual(searched.context["browse"].page.number, 1)
        self.assertEqual(searched.context["rows"][0]["training"], both)
        self.assertIn("status=published", searched.context["browse"].return_to())
        self.assertIn("q=update", searched.context["browse"].return_to())
        self.assertContains(searched, "return_to=")
