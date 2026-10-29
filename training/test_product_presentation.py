"""Keep ordinary product screens free of implementation narration."""
from datetime import timedelta
from html.parser import HTMLParser

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Assignment, Employee, Notification, Question, QuestionOption, Quiz, Slide, Training, TrainingVersion


class PageText(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.omitted = 0
        self.parts = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "textarea"}:
            self.omitted += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "textarea"}:
            self.omitted -= 1

    def handle_data(self, data):
        if not self.omitted:
            self.parts.append(data)

    @property
    def text(self):
        return " ".join(" ".join(self.parts).split())


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="admin@example.test")
class ProductPresentationTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.admin = Employee.objects.create(name="Administrator", email="admin@example.test", role="administrator", is_demo=True)
        self.training = Training.objects.create(is_demo=True)
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        Slide.objects.create(version=self.version, position=1, header="Read", body="Take care")
        self.quiz = Quiz.objects.create(is_demo=True, title="Safety check", passing_percent=75)
        self.ordering = Question.objects.create(quiz=self.quiz, position=1, type="ordering", prompt="Order these steps")
        for position, text in enumerate(("Inspect", "Report"), 1):
            QuestionOption.objects.create(question=self.ordering, position=position, text=text, required_rank=position)

    def test_core_pages_have_product_copy_and_keep_form_revision_guards(self):
        destinations = [reverse("training_list"), reverse("employee_directory"),
            reverse("edit_employee", args=[self.admin.pk]), reverse("reports"), reverse("group_list"),
            reverse("assignment_dashboard"), reverse("demo_switch"), reverse("draft_editor", args=[self.training.pk]),
            reverse("library_quiz_editor", args=[self.quiz.pk])]
        for url in destinations:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            text = PageText(response.content.decode()).text
            for artifact in ("Showing saved revision", "Local sink address", "synthetically confirmed", "Local development only",
                             "Completion =", "Synthetic records for local development", "server is at revision"):
                with self.subTest(url=url, artifact=artifact):
                    self.assertNotIn(artifact, text)
        editor = self.client.get(reverse("library_quiz_editor", args=[self.quiz.pk]))
        self.assertContains(editor, 'name="revision" value="0"')

    def test_ordering_labels_are_short_and_still_associated_with_their_controls(self):
        response = self.client.get(reverse("quiz_learner_preview", args=[self.quiz.pk]))
        self.assertContains(response, "Step 1")
        self.assertContains(response, "Step 2")
        self.assertContains(response, f'for="order-{self.ordering.pk}-1"')
        self.assertContains(response, f'name="order_{self.ordering.pk}"')
        self.assertNotContains(response, "Position 1 for question")
        self.assertNotContains(response, "answer keys are hidden")

    def test_saved_and_conflict_feedback_does_not_expose_internal_counters(self):
        quiz = Quiz.objects.create(is_demo=True)
        url = reverse("library_quiz_editor", args=[quiz.pk])
        payload = {"revision": 0, "title": "New quiz", "passing_percent": "75", "action": "save"}
        saved = self.client.post(url, payload, follow=True)
        self.assertContains(saved, "Saved at")
        self.assertNotIn("revision", PageText(saved.content.decode()).text.lower())
        conflict = self.client.post(url, payload)
        self.assertEqual(conflict.status_code, 409)
        self.assertContains(conflict, "Replace the saved quiz with my changes", status_code=409)
        self.assertContains(conflict, 'name="revision" value="1"', status_code=409)
        self.assertNotIn("revision", PageText(conflict.content.decode()).text.lower())

    def test_notification_labels_are_plain_without_changing_the_recorded_state(self):
        TrainingVersion.objects.filter(pk=self.version.pk).update(status="published")
        assignment = Assignment.objects.create(employee=self.admin, training=self.training, version=self.version,
            due_date=timezone.localdate(), due_timezone="America/New_York", due_at=timezone.now()+timedelta(days=1))
        notification = Notification.objects.create(assignment=assignment, kind="assigned", due_revision=0, status="sent")
        response = self.client.get(reverse("notification_detail", args=[notification.pk]))
        self.assertContains(response, "Processed")
        self.assertNotContains(response, "local sink")
        self.assertNotContains(response, "Deadline revision")
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
