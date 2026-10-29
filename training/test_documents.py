import copy
import csv
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from datetime import timedelta

from django.db import close_old_connections, connection, connections
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignments import acknowledge_slide, create_assignments, slides_acknowledged, visit_document
from .documents import (MAX_BODY_BYTES, body_text, convert_draft, documents, edit_outline,
                        outline, plain_body, render_body, validate_body)
from .models import (Assignment, AssignmentDocumentProgress, Employee, OutlineItem,
                     Question, QuestionOption, Quiz, Slide, Training, TrainingVersion)
from .quizzes import QuizSubmissionError, submit_quiz
from .services import DraftConflict, DraftInputError, PublicationBlocked, create_revision, create_training, publish_saved_draft
from .training_imports import new_batch, parse_file, update_batch


class StructuredBodyTests(SimpleTestCase):
    def test_rich_content_round_trips_and_html_is_escaped(self):
        body = {"type": "doc", "content": [
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Safety"}]},
            {"type": "blockquote", "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "<script>alert(1)</script>", "marks": [{"type": "strong"}, {"type": "em"}]},
                {"type": "hard_break"}, {"type": "text", "text": "Policy", "marks": [{"type": "link", "attrs": {"href": "https://example.test/policy"}}]}]}]},
            {"type": "ordered_list", "attrs": {"order": 2}, "content": [{"type": "list_item", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Report"}]}]}]},
        ]}
        before = copy.deepcopy(body)
        self.assertEqual(validate_body(json.loads(json.dumps(body))), body)
        rendered = str(render_body(body))
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn('<ol start="2">', rendered)
        self.assertEqual(body, before)

    def test_unsupported_nodes_nesting_attributes_and_links_are_rejected(self):
        invalid = [plain_body("\ud800"), None, [], {"type": "doc", "content": []},
            {"type": "doc", "content": [{"type": "script"}]},
            {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "paragraph"}]}]},
            {"type": "doc", "content": [{"type": "list_item", "content": [5]}]},
            {"type": "doc", "attrs": {"onclick": "evil"}, "content": [{"type": "paragraph"}]}]
        for href in ["javascript:alert(1)", "data:text/html,bad", "http://example.test", "https://a\n.test", "https://user:password@example.test"]:
            body = plain_body("Link")
            body["content"][0]["content"][0]["marks"] = [{"type": "link", "attrs": {"href": href}}]
            invalid.append(body)
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(DraftInputError):
                validate_body(body)

    def test_explicit_body_size_and_depth_limits(self):
        with self.assertRaises(DraftInputError):
            validate_body(plain_body("x" * MAX_BODY_BYTES))
        body = plain_body("deep")
        for _ in range(13):
            body = {"type": "doc", "content": [{"type": "blockquote", "content": body["content"]}]}
        with self.assertRaises(DraftInputError):
            validate_body(body)


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="admin@example.test")
class DocumentWorkflowTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.admin = Employee.objects.create(name="Admin", email="admin@example.test", role="administrator", is_demo=True)
        self.learner = Employee.objects.create(name="Learner", email="learner@example.test", is_demo=True)
        self.training = create_training(content_format="documents")
        self.version = self.training.versions.get()
        self.url = reverse("draft_editor", args=[self.training.pk])

    def edit(self, action, **kwargs):
        self.version.refresh_from_db()
        version, target = edit_outline(version_id=self.version.pk, revision=self.version.revision, action=action, **kwargs)
        self.version = version
        return target

    def doc(self, parent=None, title="Read"):
        item = self.edit("add_document", parent=parent)
        return self.edit("save", target=item.pk, title=title, body=plain_body("Read carefully"), training_title="Safety")

    def publish_assign(self, quiz=False):
        if quiz:
            attached = Quiz.objects.create(version=self.version, passing_percent=75)
            question = Question.objects.create(quiz=attached, position=1, type="single_choice", prompt="Report?")
            correct = QuestionOption.objects.create(question=question, position=1, text="Yes", is_correct=True)
            QuestionOption.objects.create(question=question, position=2, text="No")
            self.answer = {question.pk: correct.pk}
        publish_saved_draft(version_id=self.version.pk, expected_revision=self.version.revision)
        create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.pk,
            employee_ids=[self.learner.pk], due_date_value=(timezone.localdate()+timedelta(days=2)).isoformat())
        return Assignment.objects.get(employee=self.learner)

    def test_outline_moves_keep_identity_and_copies_receive_independent_identities(self):
        outer = self.edit("add_section")
        inner = self.edit("add_section", parent=outer.pk)
        first = self.doc(inner.pk)
        second = self.doc()
        self.edit("move", target=second.pk, parent=inner.pk, position=1)
        self.assertEqual([item.pk for item in documents(self.version)], [second.pk, first.pk])
        copied = self.edit("duplicate", target=outer.pk)
        self.assertNotEqual(copied.pk, outer.pk)
        rows = outline(self.version)
        self.assertEqual(len(rows), 8)
        self.assertEqual(len({item.pk for item in rows}), 8)
        self.assertEqual([item.position for item in rows if item.parent_id == inner.pk], [1,2])
        self.edit("save", target=first.pk, body=plain_body("Changed"))
        self.assertEqual(sum(body_text(item.body) == "Changed" for item in documents(self.version)), 1)

    def test_cycle_cross_version_parent_and_document_parent_rejected_atomically(self):
        parent = self.edit("add_section")
        child = self.edit("add_section", parent=parent.pk)
        doc = self.doc()
        other = TrainingVersion.objects.create(training=Training.objects.create(), version_number=1, content_format="documents")
        foreign = OutlineItem.objects.create(version=other, kind="section", position=1, title="Other")
        for target, destination in [(parent.pk, child.pk), (child.pk, doc.pk), (doc.pk, foreign.pk)]:
            revision = self.version.revision
            with self.assertRaises(DraftInputError):
                self.edit("move", target=target, parent=destination, position=1)
            self.version.refresh_from_db()
            self.assertEqual(self.version.revision, revision)
        self.assertEqual(len(outline(self.version)), 3)

    def test_removal_requires_review_and_removes_exact_descendants(self):
        section = self.edit("add_section")
        self.doc(section.pk)
        kept = self.doc()
        with self.assertRaises(DraftInputError):
            self.edit("remove", target=section.pk)
        response = self.client.post(self.url, {"revision": self.version.revision, "target": section.pk, "action": "review_remove"})
        self.assertContains(response, "Remove 2 outline items")
        self.assertEqual(self.version.outline_items.count(), 3)
        self.edit("remove", target=section.pk, confirm=True)
        self.assertEqual([item.pk for item in outline(self.version)], [kept.pk])

    def test_stale_edit_has_precedence_and_retains_submitted_content(self):
        item = self.doc()
        response = self.client.post(self.url, {"revision": 0, "target": item.pk, "action": "save",
            "item_title": "My unsaved title", "body": "malformed JSON"})
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "My unsaved title", status_code=409)
        self.assertContains(response, "malformed JSON", status_code=409)
        item.refresh_from_db()
        self.assertEqual(item.title, "Read")

    def test_edit_to_removed_document_retains_submitted_content(self):
        item = self.doc()
        stale_revision = self.version.revision
        self.edit("remove", target=item.pk, confirm=True)
        response = self.client.post(self.url, {"revision": stale_revision, "target": item.pk, "action": "save",
            "item_title": "My unsaved title", "body": "my unsaved body"})
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "My unsaved title", status_code=409)
        self.assertContains(response, "my unsaved body", status_code=409)

    def test_invalid_body_rerenders_and_does_not_save_title(self):
        item = self.doc()
        response = self.client.post(self.url, {"revision": self.version.revision, "target": item.pk,
            "action": "save", "item_title": "Unsaved", "body": json.dumps({"type":"script"})})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'value="Unsaved"', status_code=400)
        item.refresh_from_db()
        self.assertEqual(item.title, "Read")

    def test_publication_requires_named_nonempty_documents_and_freezes_content(self):
        self.edit("add_document")
        with self.assertRaises(PublicationBlocked):
            publish_saved_draft(version_id=self.version.pk, expected_revision=self.version.revision)
        item = documents(self.version)[0]
        self.edit("save", target=item.pk, title="Read", body=plain_body("Text"), training_title="Safety")
        self.publish_assign()
        item.title = "Changed"
        with self.assertRaises(ValidationError):
            item.save()
        with self.assertRaises(DraftConflict):
            self.edit("save", target=item.pk, title="Changed")
        response = self.client.get(reverse("publication_review", args=[self.training.pk]))
        self.assertEqual(response.status_code,302)

    def test_author_to_learner_quiz_and_document_report_export(self):
        section = self.edit("add_section")
        self.edit("save", target=section.pk, title="Equipment")
        one = self.doc(section.pk, title="Inspect")
        two = self.doc(section.pk, title="Report")
        assignment = self.publish_assign(quiz=True)
        with self.assertRaises(QuizSubmissionError):
            submit_quiz(assignment_id=assignment.pk, employee=self.learner, submission_id=uuid.uuid4(), answers=self.answer)
        visit_document(assignment_id=assignment.pk, document_id=one.pk, employee=self.learner)
        self.assertFalse(slides_acknowledged(assignment))
        assignment.refresh_from_db()
        self.assertEqual(assignment.resume_document_id, one.pk)
        visit_document(assignment_id=assignment.pk, document_id=one.pk, employee=self.learner, acknowledge=True)
        visit_document(assignment_id=assignment.pk, document_id=one.pk, employee=self.learner, acknowledge=True)
        visit_document(assignment_id=assignment.pk, document_id=two.pk, employee=self.learner, acknowledge=True)
        assignment.refresh_from_db()
        self.assertIsNone(assignment.completed_at)
        self.assertTrue(slides_acknowledged(assignment))
        attempt = submit_quiz(assignment_id=assignment.pk, employee=self.learner, submission_id=uuid.uuid4(), answers=self.answer)
        self.assertTrue(attempt.passed)
        params = {"tab":"document", "version":self.version.pk}
        report = self.client.get(reverse("training_report", args=[self.training.pk]), params)
        self.assertContains(report, "Equipment / Inspect")
        self.assertEqual([row["acknowledged"] for row in report.context["rows"]], [1,1])
        exported = self.client.get(reverse("training_report_export", args=[self.training.pk]),params)
        data = list(csv.DictReader(io.StringIO(exported.content.decode())))
        self.assertEqual(len(data),2)
        self.assertEqual(data[0]["Document"], "Equipment / Inspect")
        detail = self.client.get(reverse("training_report",args=[self.training.pk]),{"version":self.version.pk,"document":one.pk})
        self.assertEqual(detail.context["summary"]["total"],1)
        self.assertContains(detail,"2/2 documents")

    def test_slide_only_document_assignment_completes_after_every_document(self):
        first, second = self.doc(), self.doc(title="Second")
        assignment = self.publish_assign()
        visit_document(assignment_id=assignment.pk, document_id=first.pk, employee=self.learner, acknowledge=True)
        assignment.refresh_from_db()
        self.assertIsNone(assignment.completed_at)
        visit_document(assignment_id=assignment.pk, document_id=second.pk, employee=self.learner, acknowledge=True)
        assignment.refresh_from_db()
        self.assertIsNotNone(assignment.completed_at)
        self.assertEqual(assignment.document_progress.count(),2)

    def test_document_learner_routes_and_permissions(self):
        item = self.doc()
        assignment = self.publish_assign()
        self.admin.role = "employee"
        self.admin.save()
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertEqual(self.client.get(reverse("assignment_document",args=[assignment.pk,item.pk])).status_code,404)
        self.admin.role = "administrator"
        self.admin.save()
        # Switch the demo actor using the existing session contract.
        session=self.client.session
        session["demo_employee_id"]=str(self.learner.pk)
        session.save()
        self.assertContains(self.client.get(reverse("assignment_detail",args=[assignment.pk])), "documents acknowledged")
        response=self.client.get(reverse("assignment_document",args=[assignment.pk,item.pk]))
        self.assertContains(response,"Read carefully")
        self.assertEqual(assignment.document_progress.count(),0)
        self.assertEqual(self.client.post(reverse("acknowledge_document",args=[assignment.pk,item.pk])).status_code,302)
        self.assertEqual(assignment.document_progress.count(),1)

    def test_legacy_revision_conversion_preserves_text_order_urls_and_evidence(self):
        legacy = create_training().versions.get()
        legacy.title="Legacy"
        legacy.save()
        slide=Slide.objects.create(version=legacy,position=1,header="Original",body="  text\n<script>x</script>",footer="Footer\n exact ")
        publish_saved_draft(version_id=legacy.pk,expected_revision=0)
        create_assignments(operation_id=uuid.uuid4(),actor=self.admin,version_id=legacy.pk,employee_ids=[self.learner.pk],due_date_value=timezone.localdate().isoformat())
        assignment=Assignment.objects.get(version=legacy)
        acknowledge_slide(assignment_id=assignment.pk,slide_id=slide.pk,employee=self.learner)
        draft=create_revision(legacy.training_id,content_format="documents")
        item=documents(draft)[0]
        self.assertEqual(item.title,slide.header)
        self.assertEqual(item.body,plain_body(slide.body,footer=slide.footer))
        self.assertEqual(assignment.slide_progress.count(),1)
        self.assertEqual(assignment.document_progress.count(),0)
        self.assertEqual(self.client.get(reverse("preview",args=[legacy.training_id,legacy.pk])).status_code,200)
        self.assertTrue(Slide.objects.filter(pk=slide.pk).exists())

    def test_new_ui_creation_and_existing_draft_conversion(self):
        response=self.client.post(reverse("new_training"),{"content_format":"documents"},follow=True)
        self.assertContains(response,"Training outline")
        self.assertContains(response,"Add document")
        legacy=create_training().versions.get()
        slide=Slide.objects.create(version=legacy,position=1,header="Read",body="Body",footer="Footer")
        response=self.client.post(reverse("convert_documents",args=[legacy.training_id]),{"revision":0,"confirm":"convert"},follow=True)
        self.assertContains(response,"Read")
        legacy.refresh_from_db()
        self.assertEqual(legacy.content_format,"documents")
        self.assertEqual(legacy.slides.count(),0)
        self.assertEqual(documents(legacy)[0].body,plain_body("Body",footer="Footer"))

    def test_document_import_preserves_headings_and_legacy_batches_keep_slides(self):
        item=parse_file("guide.md",b"# Safety\nIntro\n## First\nRead\n```md\n## Code\n```\n## Second\nTry",content_format="documents")
        self.assertEqual(item["content_format"],"documents")
        blocks=item["documents"][0]["body"]["content"]
        self.assertEqual([b["type"] for b in blocks],["paragraph","heading","paragraph","heading","paragraph"])
        batch=new_batch(self.admin,[item])
        result=update_batch(batch_id=batch.pk,owner=self.admin,revision=0,titles={item["id"]:"Edited"},commit=True)
        version=TrainingVersion.objects.get(training_id=result.result_ids[0])
        self.assertEqual(version.content_format,"documents")
        self.assertEqual(documents(version)[0].title,"Edited")
        old=parse_file("old.txt",b"Old")
        batch=new_batch(self.admin,[old])
        result=update_batch(batch_id=batch.pk,owner=self.admin,revision=0,titles={},commit=True)
        self.assertEqual(TrainingVersion.objects.get(training_id=result.result_ids[0]).content_format,"slides")

    def test_excessive_outline_depth_rolls_back_and_keeps_revision(self):
        parent=None
        for _ in range(8):
            parent=self.edit("add_section",parent=parent).pk
        revision=self.version.revision
        with self.assertRaises(DraftInputError):
            self.edit("add_document",parent=parent)
        self.version.refresh_from_db()
        self.assertEqual(self.version.revision,revision)
        self.assertEqual(len(outline(self.version)),8)

    def test_document_format_revision_clones_whole_tree_without_progress(self):
        section=self.edit("add_section")
        self.edit("save",target=section.pk,title="Section")
        document=self.doc(section.pk)
        assignment=self.publish_assign()
        visit_document(assignment_id=assignment.pk,document_id=document.pk,employee=self.learner,acknowledge=True)
        draft=create_revision(self.training.pk)
        self.assertEqual(draft.content_format,"documents")
        copied=documents(draft)[0]
        self.assertNotEqual(copied.pk,document.pk)
        self.assertNotEqual(copied.parent_id,section.pk)
        self.assertEqual(copied.body,document.body)
        self.assertEqual(AssignmentDocumentProgress.objects.filter(document__version=draft).count(),0)

    def test_document_import_ui_records_its_format_and_commits_idempotently(self):
        response=self.client.post(reverse("import_trainings"),{"files":SimpleUploadedFile("read.md",b"# Read\n## First\nText")},follow=True)
        batch=response.context["batch"]
        self.assertEqual(batch.plan[0]["content_format"],"documents")
        self.assertContains(response,"1 document")
        self.assertContains(response,"First")
        endpoint=reverse("review_import",args=[batch.pk])
        response=self.client.post(endpoint,{"revision":0,"action":"commit"},follow=True)
        self.assertEqual(response.status_code,200)
        batch.refresh_from_db()
        version=TrainingVersion.objects.get(training_id=batch.result_ids[0])
        self.assertEqual(len(documents(version)),1)
        before=Training.objects.count()
        self.client.post(endpoint,{"revision":0,"action":"commit"})
        self.assertEqual(Training.objects.count(),before)

    def test_supporting_material_errors_keep_document_editor(self):
        self.doc()
        endpoint=reverse("save_materials",args=[self.training.pk])
        response=self.client.post(endpoint,{"revision":self.version.revision,"action":"add"},follow=True)
        self.assertContains(response,"Document formatting")
        self.assertContains(response,"Supporting material")
        self.version.refresh_from_db()
        material=self.version.materials.get()
        response=self.client.post(endpoint,{"revision":self.version.revision,"action":"save",
            "material_id":[str(material.pk)],"material_name":["x"*201],"material_url":["https://example.test"],"material_description":[""]})
        self.assertEqual(response.status_code,400)
        self.assertContains(response,"Document formatting",status_code=400)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class ConcurrentDocumentTests(TransactionTestCase):
    def parallel(self, work):
        barrier=Barrier(2)
        def run(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return work(index)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run,range(2)))

    def test_simultaneous_outline_saves_accept_one_revision(self):
        version=create_training(content_format="documents").versions.get()
        def work(index):
            try:
                edit_outline(version_id=version.pk,revision=0,action="add_document")
                return "saved"
            except DraftConflict:
                return "conflict"
        self.assertCountEqual(self.parallel(work),["saved","conflict"])
        version.refresh_from_db()
        self.assertEqual(version.revision,1)
        self.assertEqual(version.outline_items.count(),1)

    def test_simultaneous_acknowledgments_record_one_completion(self):
        version=create_training(content_format="documents").versions.get()
        version,item=edit_outline(version_id=version.pk,revision=0,action="add_document",training_title="Safety")
        version,item=edit_outline(version_id=version.pk,revision=1,action="save",target=item.pk,title="Read",body=plain_body("Read"))
        publish_saved_draft(version_id=version.pk,expected_revision=2)
        learner=Employee.objects.create(name="Learner",email="learner@example.test",is_demo=True)
        assignment=Assignment.objects.create(employee=learner,training=version.training,version=version,
            due_date=timezone.localdate(),due_timezone="America/New_York",due_at=timezone.now()+timedelta(days=1))
        def work(index):
            actor=Employee.objects.get(pk=learner.pk)
            visit_document(assignment_id=assignment.pk,document_id=item.pk,employee=actor,acknowledge=True)
        self.parallel(work)
        assignment.refresh_from_db()
        self.assertIsNotNone(assignment.completed_at)
        self.assertEqual(assignment.document_progress.count(),1)


class DocumentMigrationTests(TransactionTestCase):
    def test_upgrade_preserves_legacy_publication_resume_and_acknowledgment(self):
        from django.db.migrations.executor import MigrationExecutor
        previous=("training","0014_importbatch")
        latest=("training","0015_trainingversion_content_format_outlineitem_and_more")
        executor=MigrationExecutor(connection)
        executor.migrate([previous])
        try:
            old=executor.loader.project_state([previous]).apps
            employee=old.get_model("training","Employee").objects.create(name="Legacy",email="legacy@example.test",is_demo=True)
            training=old.get_model("training","Training").objects.create()
            version=old.get_model("training","TrainingVersion").objects.create(training=training,version_number=1,status="published",title="Legacy",published_at=timezone.now())
            slide=old.get_model("training","Slide").objects.create(version=version,position=1,header="Original",body="Exact\n body",footer="Footer")
            assignment=old.get_model("training","Assignment").objects.create(employee=employee,training=training,version=version,
                due_date=timezone.localdate(),due_timezone="America/New_York",due_at=timezone.now(),resume_slide=slide,completed_at=timezone.now())
            progress=old.get_model("training","AssignmentSlideProgress").objects.create(assignment=assignment,slide=slide,acknowledged_at=timezone.now())
            executor=MigrationExecutor(connection)
            executor.migrate([latest])
            migrated=TrainingVersion.objects.get(pk=version.pk)
            evidence=Assignment.objects.get(pk=assignment.pk)
            self.assertEqual(migrated.content_format,"slides")
            self.assertEqual(migrated.published_at,version.published_at)
            self.assertEqual(evidence.resume_slide_id,slide.pk)
            self.assertIsNone(evidence.resume_document_id)
            self.assertEqual(evidence.completed_at,assignment.completed_at)
            self.assertEqual(evidence.slide_progress.get().pk,progress.pk)
            self.assertEqual(evidence.slide_progress.get().acknowledged_at,progress.acknowledged_at)
            self.assertEqual(Slide.objects.get(pk=slide.pk).body,"Exact\n body")
        finally:
            MigrationExecutor(connection).migrate([latest])
