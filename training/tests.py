from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Employee, Slide, Training, TrainingVersion


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
class AuthoringWorkflowTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(
            name="Demo Administrator",
            email="demo.admin@example.test",
            role=Employee.Role.ADMINISTRATOR,
            is_demo=True,
        )

    def create_training(self):
        response = self.client.post(reverse("new_training"))
        self.assertEqual(response.status_code, 302)
        training = Training.objects.get()
        return training, training.versions.get()

    def save(self, version, *, action="save", title="Data Security", description="", slides=None, revision=None):
        if slides is None:
            slides = list(version.slides.all())
        payload = {
            "revision": version.revision if revision is None else revision,
            "title": title,
            "description": description,
            "slide_id": [str(slide.id) for slide in slides],
            "slide_header": [slide.header for slide in slides],
            "slide_body": [slide.body for slide in slides],
            "slide_footer": [slide.footer for slide in slides],
            "action": "save" if action == "publish" else action,
        }
        response = self.client.post(reverse("draft_editor", args=[version.training_id]), payload)
        if action != "publish" or response.status_code != 302:
            return response
        version.refresh_from_db()
        review_url = reverse("publication_review", args=[version.training_id])
        review = self.client.get(review_url)
        if b'name="version_id"' not in review.content:
            return review
        return self.client.post(review_url, {"version_id": str(version.id), "revision": version.revision})

    def test_three_slides_reorder_preview_publish_and_revision_preserves_version_one(self):
        training, draft = self.create_training()
        for _ in range(3):
            self.assertEqual(self.save(draft, action="add").status_code, 302)
            draft.refresh_from_db()
        slides = list(draft.slides.all())
        headers = ["Introduction", "<script>alert(1)</script>", "Summary"]
        for slide, header in zip(slides, headers, strict=True):
            slide.header = header
            slide.body = "First paragraph\n\nSecond paragraph"
        self.assertEqual(self.save(draft, slides=slides, action=f"up:{slides[2].id}").status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(
            list(draft.slides.values_list("header", flat=True)),
            ["Introduction", "Summary", "<script>alert(1)</script>"],
        )
        preview = self.client.get(reverse("preview", args=[training.id, draft.id]))
        self.assertContains(preview, "First paragraph\n\nSecond paragraph")
        self.assertContains(preview, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(preview, "<script>alert(1)</script>")

        self.assertEqual(self.save(draft, action="publish").status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, TrainingVersion.Status.PUBLISHED)
        self.assertIsNotNone(draft.published_at)
        self.assertEqual(self.client.get(reverse("draft_editor", args=[training.id])).status_code, 404)
        with self.assertRaises(ValidationError):
            draft.title = "Changed title"
            draft.save()
        with self.assertRaises(ValidationError):
            slide = draft.slides.first()
            slide.body = "Changed body"
            slide.save()

        response = self.client.post(reverse("new_revision", args=[training.id]))
        self.assertEqual(response.status_code, 302)
        revision = training.versions.get(version_number=2)
        self.assertEqual(revision.status, TrainingVersion.Status.DRAFT)
        self.assertEqual(revision.slides.count(), 3)
        self.assertEqual(self.save(revision, title="Revised Security").status_code, 302)
        revision.refresh_from_db()
        first_revised_slide = revision.slides.first()
        self.assertEqual(
            self.save(revision, title="Revised Security", action=f"remove:{first_revised_slide.id}").status_code,
            302,
        )
        self.assertEqual(revision.slides.count(), 2)
        draft.refresh_from_db()
        self.assertEqual(draft.title, "Data Security")
        self.assertEqual(draft.slides.count(), 3)

    def test_incomplete_draft_saves_and_publication_lists_missing_content(self):
        _, draft = self.create_training()
        response = self.save(draft, title="", action="publish")
        self.assertContains(response, "Add a training title")
        self.assertContains(response, "Add at least one slide")
        blocked = self.client.post(reverse("publication_review", args=[draft.training_id]), {
            "version_id": str(draft.id), "revision": draft.revision,
        })
        self.assertContains(blocked, "Fix these items before publication", status_code=409)
        self.assertNotContains(blocked, 'name="version_id"', status_code=409)
        draft.refresh_from_db()
        self.assertEqual(draft.status, TrainingVersion.Status.DRAFT)
        self.assertEqual(self.save(draft, title="Safety", action="add").status_code, 302)
        draft.refresh_from_db()
        response = self.save(draft, title="Safety", action="publish")
        self.assertContains(response, "Slide 1: add a header")
        self.assertContains(response, "Slide 1: add a body")
        draft.refresh_from_db()
        slide = draft.slides.get()
        slide.header = "Safety basics"
        slide.body = "Acknowledge this slide."
        self.assertEqual(self.save(draft, title="Safety", slides=[slide], action="publish").status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, TrainingVersion.Status.PUBLISHED)

    def test_publication_review_links_second_slide_body_and_save_error_links_field(self):
        training, draft = self.create_training()
        self.assertEqual(self.save(draft, action="add").status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(self.save(draft, action="add").status_code, 302)
        draft.refresh_from_db()
        slides = list(draft.slides.all())
        slides[0].header, slides[0].body = "First", "Ready"
        slides[1].header, slides[1].body = "Second", ""
        self.assertEqual(self.save(draft, title="Safety", slides=slides).status_code, 302)
        draft.refresh_from_db()
        review = self.client.get(reverse("publication_review", args=[training.id]))
        self.assertContains(review, f"#body-{slides[1].id}")
        slides[1].body = "x" * 10001
        response = self.save(draft, title="Safety", slides=slides)
        self.assertContains(response, f'href="#body-{slides[1].id}"', status_code=400)

    def test_publication_review_uses_saved_content_and_final_submit_is_idempotent(self):
        training, draft = self.create_training()
        self.save(draft, action="add")
        draft.refresh_from_db()
        slide = draft.slides.get()
        slide.header = "Saved header"
        slide.body = "Saved body"
        self.assertEqual(self.save(draft, title="Safety", description="Saved description", slides=[slide]).status_code, 302)
        draft.refresh_from_db()
        url = reverse("publication_review", args=[training.id])
        review = self.client.get(url)
        self.assertContains(review, "Saved header")
        self.assertContains(review, "Saved body")
        self.assertContains(review, "Saved description")
        self.assertContains(review, "1 slide in saved order")
        self.assertContains(review, "No quiz in this version")
        self.assertContains(review, "assign the training afterward")
        payload = {"version_id": str(draft.id), "revision": draft.revision}
        first = self.client.post(url, payload)
        self.assertRedirects(first, reverse("preview", args=[training.id, draft.id]))
        preview = self.client.get(first.url)
        self.assertContains(preview, "Version 1 published")
        self.assertContains(preview, "Assign training")
        self.assertContains(preview, f"?version_id={draft.id}")
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertEqual(training.versions.count(), 1)
        draft.refresh_from_db()
        self.assertEqual((draft.title, draft.description, draft.revision), ("Safety", "Saved description", payload["revision"]))
        self.assertEqual(list(draft.slides.values_list("header", "body")), [("Saved header", "Saved body")])

    def test_changed_saved_revision_requires_fresh_review_before_publication(self):
        training, draft = self.create_training()
        self.save(draft, action="add")
        draft.refresh_from_db()
        slide = draft.slides.get()
        slide.header = "First header"
        slide.body = "First body"
        self.save(draft, title="Safety", slides=[slide])
        draft.refresh_from_db()
        reviewed_revision = draft.revision
        url = reverse("publication_review", args=[training.id])
        self.assertContains(self.client.get(url), "First header")
        slide.header = "Changed header"
        self.save(draft, title="Safety", slides=[slide])
        stale = self.client.post(url, {"version_id": str(draft.id), "revision": reviewed_revision})
        self.assertContains(stale, "saved draft changed", status_code=409)
        self.assertContains(stale, "Changed header", status_code=409)
        self.assertNotContains(stale, 'name="version_id"', status_code=409)
        draft.refresh_from_db()
        self.assertEqual(draft.status, TrainingVersion.Status.DRAFT)

    def test_direct_editor_publish_is_rejected_and_employee_cannot_review(self):
        training, draft = self.create_training()
        direct = self.client.post(reverse("draft_editor", args=[training.id]), {
            "revision": draft.revision, "title": "Safety", "description": "",
            "slide_id": [], "slide_header": [], "slide_body": [], "slide_footer": [],
            "action": "publish",
        })
        self.assertEqual(direct.status_code, 400)
        draft.refresh_from_db()
        self.assertEqual((draft.title, draft.revision, draft.status), ("", 0, TrainingVersion.Status.DRAFT))
        self.admin.role = Employee.Role.EMPLOYEE
        self.admin.save()
        url = reverse("publication_review", args=[training.id])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"version_id": str(draft.id), "revision": 0}).status_code, 403)

    def test_stale_edit_is_rejected_without_overwriting_newer_draft(self):
        _, draft = self.create_training()
        self.assertEqual(self.save(draft, title="First save").status_code, 302)
        response = self.save(draft, title="Stale save", revision=0)
        self.assertEqual(response.status_code, 409)
        draft.refresh_from_db()
        self.assertEqual(draft.title, "First save")

    def test_save_confirmation_and_publication_errors_link_to_entered_fields(self):
        training, draft = self.create_training()
        self.assertEqual(self.save(draft, title="Safety", action="add").status_code, 302)
        saved = self.client.get(reverse("draft_editor", args=[training.id]))
        self.assertContains(saved, "Saved at")
        self.assertContains(saved, "Unsaved changes.")
        draft.refresh_from_db()
        slide = draft.slides.get()
        payload = {
            "revision": draft.revision, "title": "", "description": "Keep this description",
            "slide_id": [str(slide.id)], "slide_header": [""],
            "slide_body": ["Entered body"], "slide_footer": [""], "action": "save",
        }
        self.assertEqual(self.client.post(reverse("draft_editor", args=[training.id]), payload).status_code, 302)
        invalid = self.client.get(reverse("publication_review", args=[training.id]))
        self.assertContains(invalid, f'href="{reverse("draft_editor", args=[training.id])}#title"')
        self.assertContains(invalid, f'href="{reverse("draft_editor", args=[training.id])}#header-{slide.id}"')
        self.assertContains(invalid, "Keep this description")
        self.assertContains(invalid, "Entered body")
        self.assertNotContains(invalid, 'name="version_id"')

    def test_remove_review_and_cancel_preserve_unsaved_draft_fields(self):
        training, draft = self.create_training()
        self.save(draft, title="Saved", action="add")
        draft.refresh_from_db()
        slide = draft.slides.get()
        payload = {
            "revision": draft.revision, "title": "Unsaved title", "description": "",
            "slide_id": [str(slide.id)], "slide_header": ["Unsaved header"],
            "slide_body": ["Unsaved body"], "slide_footer": [""],
            "action": f"review_remove:{slide.id}",
        }
        url = reverse("draft_editor", args=[training.id])
        review = self.client.post(url, payload)
        self.assertContains(review, "Confirm removal")
        self.assertContains(review, "Unsaved header")
        self.assertContains(review, 'data-unsaved="true"')
        self.assertContains(review, "Unsaved changes are shown below.")
        draft.refresh_from_db()
        self.assertEqual((draft.revision, draft.title, draft.slides.count()), (1, "Saved", 1))
        canceled = self.client.post(url, {**payload, "action": "cancel_remove"})
        self.assertContains(canceled, "Unsaved header")
        self.assertNotContains(canceled, "Confirm removal")
        self.assertEqual(draft.slides.count(), 1)
        confirmed = self.client.post(url, {**payload, "action": f"remove:{slide.id}"})
        self.assertEqual(confirmed.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.title, "Unsaved title")
        self.assertEqual(draft.slides.count(), 0)

    def test_stale_draft_shows_both_versions_and_requires_explicit_apply(self):
        training, draft = self.create_training()
        self.save(draft, title="Server title")
        stale_payload = {
            "revision": 0, "title": "My title", "description": "My text",
            "slide_id": [], "slide_header": [], "slide_body": [],
            "slide_footer": [], "action": "save",
        }
        url = reverse("draft_editor", args=[training.id])
        conflict = self.client.post(url, stale_payload)
        self.assertContains(conflict, "Server title", status_code=409)
        self.assertContains(conflict, 'value="My title"', status_code=409)
        self.assertContains(conflict, "My text", status_code=409)
        self.assertContains(conflict, 'data-unsaved="true"', status_code=409)
        self.assertContains(conflict, "Replace the saved draft with my changes", status_code=409)
        draft.refresh_from_db()
        self.assertEqual(draft.title, "Server title")
        applied = self.client.post(url, {**stale_payload, "revision": draft.revision})
        self.assertEqual(applied.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual((draft.title, draft.description), ("My title", "My text"))

    def test_remove_confirmation_cannot_overwrite_newer_revision(self):
        training, draft = self.create_training()
        self.save(draft, action="add")
        draft.refresh_from_db()
        slide = draft.slides.get()
        payload = {
            "revision": draft.revision, "title": "Old tab", "description": "",
            "slide_id": [str(slide.id)], "slide_header": ["Old tab header"],
            "slide_body": ["Old tab body"], "slide_footer": [""],
            "action": f"review_remove:{slide.id}",
        }
        url = reverse("draft_editor", args=[training.id])
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        self.save(draft, title="Newer save")
        stale = self.client.post(url, {**payload, "action": f"remove:{slide.id}"})
        self.assertContains(stale, "A newer draft was saved", status_code=409)
        draft.refresh_from_db()
        self.assertEqual(draft.title, "Newer save")
        self.assertEqual(draft.slides.count(), 1)

    def test_published_draft_post_preserves_submitted_text_without_apply(self):
        training, draft = self.create_training()
        self.save(draft, title="Safety", action="add")
        draft.refresh_from_db()
        slide = draft.slides.get()
        slide.header = "Header"
        slide.body = "Body"
        self.save(draft, title="Safety", slides=[slide], action="publish")
        stale = self.client.post(reverse("draft_editor", args=[training.id]), {
            "revision": draft.revision, "title": "Unpublished edits", "description": "",
            "slide_id": [str(slide.id)], "slide_header": ["My header"],
            "slide_body": ["My body"], "slide_footer": [""], "action": "save",
        })
        self.assertContains(stale, "This version was published", status_code=409)
        self.assertContains(stale, "My header", status_code=409)
        self.assertNotContains(stale, "Apply my changes", status_code=409)

    def test_structurally_invalid_slide_data_is_rejected_without_saving(self):
        training, draft = self.create_training()
        response = self.client.post(
            reverse("draft_editor", args=[training.id]),
            {
                "revision": 0,
                "title": "Safety",
                "description": "",
                "slide_id": ["not-a-uuid"],
                "slide_header": ["Header"],
                "slide_body": ["Body"],
                "slide_footer": [""],
                "action": "save",
            },
        )
        self.assertEqual(response.status_code, 400)
        draft.refresh_from_db()
        self.assertEqual(draft.title, "")
        self.assertEqual(draft.slides.count(), 0)

    def test_demo_admin_requires_debug_loopback_active_administrator(self):
        url = reverse("training_list")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url, REMOTE_ADDR="192.0.2.4").status_code, 403)
        with override_settings(DEBUG=False):
            self.assertRedirects(self.client.get(url), f"{reverse('sign_in')}?next={url}",
                                 fetch_redirect_response=False)
        self.admin.role = Employee.Role.EMPLOYEE
        self.admin.save()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.admin.role = Employee.Role.ADMINISTRATOR
        self.admin.is_active = False
        self.admin.save()
        self.assertEqual(self.client.get(url).status_code, 403)
