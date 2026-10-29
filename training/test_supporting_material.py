import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignments import create_assignments
from .models import Assignment, Employee, Slide, SupportingMaterial, Training, TrainingVersion
from .services import DraftConflict, create_revision, publication_problems, save_materials


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
class SupportingMaterialTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(name="Admin", email="demo.admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.employee = Employee.objects.create(name="Alice", email="alice@example.test", is_demo=True)
        self.other = Employee.objects.create(name="Bob", email="bob@example.test", is_demo=True)
        self.training = Training.objects.create(is_demo=True)
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        Slide.objects.create(version=self.version, position=1, header="Read", body="Read")

    def test_draft_material_saves_and_invalid_links_block_publication(self):
        save_materials(version_id=self.version.id, expected_revision=0, rows=[], action="add")
        material = self.version.materials.get()
        self.assertIn("Material 1: add a name.", publication_problems(self.version))
        self.assertIn("Material 1: add a link.", publication_problems(self.version))
        for url in ("javascript:alert(1)", "data:text/plain,x", "http://example.test/file"):
            rows = [{"id": material.id, "name": "Policy", "url": url, "description": "Read this"}]
            self.version.refresh_from_db()
            save_materials(version_id=self.version.id, expected_revision=self.version.revision,
                           rows=rows, action="save")
            self.assertIn("Material 1: use an https:// link.", publication_problems(self.version))
        self.version.refresh_from_db()
        rows[0]["url"] = "https://example.test/policy"
        save_materials(version_id=self.version.id, expected_revision=self.version.revision,
                       rows=rows, action="save")
        self.assertEqual(publication_problems(self.version), [])

    def test_material_order_and_removal_persist(self):
        save_materials(version_id=self.version.id, expected_revision=0, rows=[], action="add")
        self.version.refresh_from_db()
        first = self.version.materials.get()
        first_row = {"id": first.id, "name": "First", "url": "https://example.test/first", "description": ""}
        save_materials(version_id=self.version.id, expected_revision=self.version.revision,
                       rows=[first_row], action="add")
        self.version.refresh_from_db()
        second = self.version.materials.exclude(pk=first.id).get()
        second_row = {"id": second.id, "name": "Second", "url": "https://example.test/second", "description": ""}
        save_materials(version_id=self.version.id, expected_revision=self.version.revision,
                       rows=[first_row, second_row], action=f"up:{second.id}")
        self.assertEqual(list(self.version.materials.values_list("name", flat=True)), ["Second", "First"])
        self.version.refresh_from_db()
        save_materials(version_id=self.version.id, expected_revision=self.version.revision,
                       rows=[second_row, first_row], action=f"remove:{first.id}")
        self.assertEqual(list(self.version.materials.values_list("name", flat=True)), ["Second"])

    def test_published_material_is_immutable_and_revision_copies_it(self):
        item = SupportingMaterial.objects.create(version=self.version, position=1, name="Policy",
                                                 url="https://example.test/policy")
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.save()
        item.name = "Changed"
        with self.assertRaises(ValidationError):
            item.save()
        with self.assertRaises(ValidationError):
            item.delete()
        draft = create_revision(self.training.id)
        copied = draft.materials.get()
        self.assertEqual(copied.name, "Policy")
        save_materials(version_id=draft.id, expected_revision=draft.revision,
                       rows=[{"id": copied.id, "name": "Updated", "url": copied.url,
                              "description": ""}], action="save")
        self.assertEqual(self.version.materials.get().name, "Policy")
        with self.assertRaises(DraftConflict):
            save_materials(version_id=draft.id, expected_revision=0, rows=[], action="save")

    def test_assigned_employee_sees_safe_descriptive_link_and_others_do_not(self):
        SupportingMaterial.objects.create(version=self.version, position=1, name="Company policy",
                                          url="https://example.test/policy", description="Reference copy")
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.save()
        create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                           employee_ids=[self.employee.id],
                           due_date_value=(timezone.now() + timedelta(days=2)).date().isoformat())
        assignment = Assignment.objects.get(employee=self.employee)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        detail = self.client.get(reverse("assignment_detail", args=[assignment.id]))
        self.assertContains(detail, "Company policy")
        self.assertContains(detail, "opens in a new tab")
        self.assertContains(detail, 'rel="noopener noreferrer"')
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.other.id)})
        self.assertNotContains(self.client.get(reverse("assignment_detail", args=[assignment.id])),
                               "Company policy", status_code=404)

    def test_editor_form_adds_and_saves_material(self):
        url = reverse("save_materials", args=[self.training.id])
        self.assertEqual(self.client.post(url, {"revision": "0", "action": "add"}).status_code, 302)
        self.version.refresh_from_db()
        item = self.version.materials.get()
        response = self.client.post(url, {"revision": str(self.version.revision), "action": "save",
                                          "material_id": [str(item.id)], "material_name": ["Policy"],
                                          "material_url": ["https://example.test/policy"],
                                          "material_description": ["Reference copy"]})
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(reverse("draft_editor", args=[self.training.id])), "Policy")
