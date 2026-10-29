from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Employee, ImportBatch, Training
from .training_imports import MAX_FILE_BYTES, MAX_REQUEST_BYTES, ImportInputError, new_batch, parse_file, update_batch


class TextConversionTests(SimpleTestCase):
    def test_markdown_title_preamble_slides_lower_headings_and_fences(self):
        item = parse_file("guide.md", b"# Safety\nIntro\n## First\nRead\n### Lower heading\n```md\n## Code heading\n```\n## Second\nTry")
        self.assertEqual(item["title"], "Safety")
        self.assertEqual([s["header"] for s in item["slides"]], ["Introduction", "First", "Second"])
        self.assertIn("### Lower heading", item["slides"][1]["body"])
        self.assertIn("## Code heading", item["slides"][1]["body"])
        self.assertNotIn("# Safety", item["slides"][0]["body"])

    def test_text_and_markdown_without_h2_create_one_slide_and_keep_html_literal(self):
        for filename, content, title in (("hello.txt", b"<script>alert(1)</script>", "hello"),
                                         ("hello.md", b"# New title\nBody", "New title")):
            item = parse_file(filename, content)
            self.assertEqual(item["title"], title)
            self.assertEqual(len(item["slides"]), 1)
        self.assertIn("empty", " ".join(parse_file("empty.md", b"")["warnings"]))

    def test_invalid_encoding_binary_type_size_and_conversion_limits_are_rejected(self):
        cases = [("file.pdf", b"text"), ("file.txt", b"\xff"), ("renamed.txt", b"%PDF-1.0"),
                 ("renamed.md", b"PK\x03\x04"), ("large.txt", b"a" * (MAX_FILE_BYTES + 1)),
                 ("body.md", b"a" * 10001), ("heading.md", b"## " + b"a" * 201),
                 ("slides.md", b"## Slide\nbody\n" * 101)]
        for filename, content in cases:
            with self.subTest(filename=filename), self.assertRaises(ImportInputError):
                parse_file(filename, content)

    def test_empty_title_and_code_heading_do_not_infer_structure(self):
        item = parse_file("file.md", b"#\n~~~md\n## Not a slide\n~~~\nText")
        self.assertEqual(item["title"], "")
        self.assertEqual(len(item["slides"]), 1)
        self.assertIn("## Not a slide", item["slides"][0]["body"])


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="admin@example.test")
class TrainingImportTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.owner = Employee.objects.create(name="Admin", email="admin@example.test", role="administrator", is_demo=True)
        self.item = parse_file("safety.md", b"# Safety\n## Read\nRead this")
        self.batch = new_batch(self.owner, [self.item])

    def mutate(self, **changes):
        return update_batch(batch_id=self.batch.pk, owner=self.owner, revision=0, titles={}, **changes)

    def test_commit_creates_ordinary_draft_and_replay_returns_same_results(self):
        first = self.mutate(commit=True)
        replay = self.mutate(commit=True)
        self.assertEqual(first.result_ids, replay.result_ids)
        self.assertEqual(Training.objects.count(), 1)
        version = Training.objects.get().versions.get()
        self.assertEqual((version.title, version.status, version.version_number), ("Safety", "draft", 1))
        self.assertEqual(version.slides.get().body, "Read this")
        self.assertEqual(version.assignments.count(), 0)

    def test_creation_failure_rolls_back_all_drafts_and_batch_results(self):
        self.batch.plan.append(parse_file("second.txt", b"Second"))
        self.batch.save()
        from .services import save_draft
        calls = 0

        def fail_second(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Failed")
            return save_draft(**kwargs)

        with patch("training.training_imports.save_draft", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.mutate(commit=True)
        self.assertEqual(Training.objects.count(), 0)
        self.batch.refresh_from_db()
        self.assertIsNone(self.batch.committed_at)
        self.assertEqual(self.batch.result_ids, [])

    def test_owner_revision_and_expiry_prevent_stale_creations(self):
        other = Employee.objects.create(name="Other", email="other@example.test", role="administrator", is_demo=True)
        with self.assertRaises(ImportBatch.DoesNotExist):
            update_batch(batch_id=self.batch.pk, owner=other, revision=0, titles={}, commit=True)
        self.mutate()
        with self.assertRaisesMessage(ImportInputError, "changed"):
            self.mutate(commit=True)
        self.batch.expires_at = timezone.now() - timedelta(seconds=1)
        self.batch.save()
        with self.assertRaisesMessage(ImportInputError, "expired"):
            self.mutate(commit=True)
        self.assertEqual(Training.objects.count(), 0)

    def test_review_title_edits_removal_and_validation_preserve_drafts_boundary(self):
        url = reverse("review_import", args=[self.batch.pk])
        response = self.client.post(url, {"revision": "0", "action": "save", f"title_{self.item['id']}": "Renamed"})
        self.assertEqual(response.status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.plan[0]["title"], "Renamed")
        self.assertEqual(Training.objects.count(), 0)
        response = self.client.post(url, {"revision": "1", "action": "commit", f"title_{self.item['id']}": "a" * 201})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "200 characters", status_code=400)
        response = self.client.post(url, {"revision": "1", "action": f"remove:{self.item['id']}"})
        self.assertEqual(response.status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.plan, [])
        with self.assertRaisesMessage(ImportInputError, "at least one"):
            update_batch(batch_id=self.batch.pk, owner=self.owner, revision=2, titles={}, commit=True)

    def test_upload_retains_valid_files_with_named_errors_and_escapes_preview(self):
        response = self.client.post(reverse("import_trainings"), {"files": [
            SimpleUploadedFile("good.txt", b"<script>alert(1)</script>"),
            SimpleUploadedFile("bad.pdf", b"%PDF-1.0"),
        ]})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "bad.pdf", status_code=400)
        self.assertContains(response, "&lt;script&gt;", status_code=400)
        self.assertNotContains(response, "<script>alert", status_code=400)
        self.assertEqual(len(response.context["batch"].plan), 1)
        self.assertEqual(Training.objects.count(), 0)

    def test_upload_limits_apply_to_streams_and_requests(self):
        url = reverse("import_trainings")
        response = self.client.post(url, {"files": SimpleUploadedFile("large.txt", b"x" * (MAX_FILE_BYTES + 1))})
        self.assertContains(response, "at most 1 MB", status_code=400)
        self.assertEqual(self.client.post(url, data=b"", content_type="multipart/form-data; boundary=x",
                                          CONTENT_LENGTH=MAX_REQUEST_BYTES + 1).status_code, 413)
        files = [SimpleUploadedFile(f"{i}.txt", b"Hello") for i in range(6)]
        response = self.client.post(url, {"files": files})
        self.assertContains(response, "at most five", status_code=400)

    def test_import_pages_require_admin_and_post_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")
        url = reverse("import_trainings")
        self.assertEqual(csrf_client.post(url, {"files": SimpleUploadedFile("a.txt", b"text")}).status_code, 403)
        self.assertEqual(csrf_client.post(reverse("review_import", args=[self.batch.pk]),
                                          {"revision": "0", "action": "commit"}).status_code, 403)
        csrf_client.get(url)
        token = csrf_client.cookies["csrftoken"].value
        response = csrf_client.post(url, {"csrfmiddlewaretoken": token, "files": SimpleUploadedFile("a.txt", b"text")})
        self.assertEqual(response.status_code, 302)
        learner = Employee.objects.create(name="Learner", email="learner@example.test", is_demo=True)
        session = self.client.session
        session["demo_employee_id"] = str(learner.pk)
        session.save()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(reverse("review_import", args=[self.batch.pk])).status_code, 403)

    def test_other_admin_get_is_404_and_expired_review_is_410(self):
        other = Employee.objects.create(name="Other", email="other@example.test", role="administrator", is_demo=True)
        session = self.client.session
        session["demo_employee_id"] = str(other.pk)
        session.save()
        self.assertEqual(self.client.get(reverse("review_import", args=[self.batch.pk])).status_code, 404)
        session["demo_employee_id"] = str(self.owner.pk)
        session.save()
        self.batch.expires_at = timezone.now() - timedelta(seconds=1)
        self.batch.save()
        self.assertEqual(self.client.get(reverse("review_import", args=[self.batch.pk])).status_code, 410)

    def test_cleanup_removes_old_records_but_keeps_drafts(self):
        committed = self.mutate(commit=True)
        ImportBatch.objects.filter(pk=committed.pk).update(committed_at=timezone.now() - timedelta(days=8))
        expired = new_batch(self.owner, [self.item], now=timezone.now() - timedelta(hours=2))
        recent = new_batch(self.owner, [self.item])
        call_command("cleanup_training_imports", stdout=StringIO())
        self.assertFalse(ImportBatch.objects.filter(pk__in=[committed.pk, expired.pk]).exists())
        self.assertTrue(ImportBatch.objects.filter(pk=recent.pk).exists())
        self.assertEqual(Training.objects.count(), 1)
        self.assertEqual(self.client.post(reverse("review_import", args=[committed.pk]),
                                          {"action": "commit", "revision": "0"}).status_code, 404)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class ConcurrentImportTests(TransactionTestCase):
    def test_simultaneous_commit_creates_one_result_set(self):
        owner = Employee.objects.create(name="Admin", email="admin@example.test", role="administrator", is_demo=True)
        batch = new_batch(owner, [parse_file("read.txt", b"Read")])
        barrier = Barrier(2)

        def commit():
            close_old_connections()
            try:
                actor = Employee.objects.get(pk=owner.pk)
                barrier.wait(timeout=10)
                return update_batch(batch_id=batch.pk, owner=actor, revision=0, titles={}, commit=True).result_ids
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: commit(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(Training.objects.count(), 1)
