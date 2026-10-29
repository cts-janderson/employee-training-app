from html.parser import HTMLParser

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Employee, Quiz, Training


class CreationActionsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.menu_depth = 0
        self.forms = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            if self.menu_depth:
                self.menu_depth += 1
            elif "create-menu" in attrs.get("class", "").split():
                self.menu_depth = 1
        if self.menu_depth and tag == "form":
            self.forms.append(attrs)
        if self.menu_depth and tag == "a":
            self.links.append(attrs)

    def handle_endtag(self, tag):
        if tag == "div" and self.menu_depth:
            self.menu_depth -= 1


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="admin@example.test")
class CreationActionTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")
        Employee.objects.create(name="Admin", email="admin@example.test", role="administrator", is_demo=True)
        response = self.client.get(reverse("training_list"))
        self.assertEqual(response.status_code, 200)
        self.menu = CreationActionsParser()
        self.menu.feed(response.content.decode())
        self.csrf = self.client.cookies["csrftoken"].value

    def test_create_content_has_a_working_link_when_scripting_is_unavailable(self):
        response = self.client.get(reverse("training_list"))
        self.assertContains(response, 'href="/trainings/create/"')
        destination = self.client.get("/trainings/create/")
        self.assertEqual(destination.status_code, 200)
        self.assertContains(destination, reverse("new_training"))
        self.assertContains(destination, reverse("new_library_quiz"))
        self.assertContains(destination, reverse("import_trainings"))

    def test_training_menu_action_creates_a_draft_and_opens_its_editor(self):
        form = next(form for form in self.menu.forms if form["action"] == reverse("new_training"))
        self.assertEqual(form["method"], "post")
        response = self.client.post(form["action"], {"csrfmiddlewaretoken": self.csrf}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Training.objects.count(), 1)
        training = Training.objects.get()
        self.assertEqual(training.versions.get().status, "draft")
        self.assertEqual(response.resolver_match.url_name, "draft_editor")

    def test_library_quiz_menu_action_creates_a_quiz_and_opens_its_editor(self):
        form = next(form for form in self.menu.forms if form["action"] == reverse("new_library_quiz"))
        response = self.client.post(form["action"], {"csrfmiddlewaretoken": self.csrf}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Quiz.objects.filter(version__isnull=True).count(), 1)
        self.assertEqual(response.resolver_match.url_name, "library_quiz_editor")

    def test_import_menu_action_reviews_creates_and_opens_an_ordinary_draft(self):
        link = next(link for link in self.menu.links if link["href"] == reverse("import_trainings"))
        response = self.client.get(link["href"])
        self.assertEqual(response.status_code, 200)
        response = self.client.post(link["href"], {"csrfmiddlewaretoken": self.csrf,
            "files": SimpleUploadedFile("safety.md", b"# Safety\n## Read\nRead this")}, follow=True)
        self.assertEqual(response.resolver_match.url_name, "review_import")
        self.assertEqual(Training.objects.count(), 0)
        batch = response.context["batch"]
        response = self.client.post(reverse("review_import", args=[batch.pk]), {
            "csrfmiddlewaretoken": self.csrf, "revision": batch.revision, "action": "commit",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        training = Training.objects.get()
        self.assertContains(response, reverse("draft_editor", args=[training.pk]))
        response = self.client.get(reverse("draft_editor", args=[training.pk]))
        self.assertContains(response, "Read this")
