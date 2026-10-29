"""Bounded plain-text conversion, independent of HTTP and draft persistence."""
import re
import uuid
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from .models import ImportBatch, OutlineItem, Slide
from .services import create_training, save_draft

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 5
MAX_REQUEST_BYTES = MAX_FILES * MAX_FILE_BYTES + 64 * 1024


class ImportInputError(ValueError):
    pass


def review_warnings(item):
    if item.get("content_format") == "documents":
        from .documents import body_text
        warnings = ["Review the imported content before publishing."]
        if not item["title"].strip():
            warnings.append("The training title is empty; add a title before publication.")
        if not body_text(item["documents"][0]["body"]).strip():
            warnings.append("The document is empty; add content before publication.")
        return warnings
    warnings = ["Review the imported content before publishing."]
    if not item["title"].strip():
        warnings.append("The training title is empty; add a title before publication.")
    if any(not slide["header"].strip() for slide in item["slides"]):
        warnings.append("One or more slide headings are empty; review publication requirements.")
    if any(not slide["body"].strip() for slide in item["slides"]):
        warnings.append("One or more slide bodies are empty; review publication requirements.")
    return warnings


def validate_plan_item(item):
    if item.get("content_format") == "documents":
        from .documents import validate_body
        from .services import DraftInputError
        if len(item["title"]) > 200:
            raise ImportInputError("Training titles must be at most 200 characters. Rename before creating drafts.")
        try:
            for document in item["documents"]:
                validate_body(document["body"])
        except DraftInputError as exc:
            raise ImportInputError(str(exc)) from None
        return
    if len(item["title"]) > 200:
        raise ImportInputError("Training titles must be at most 200 characters. Rename before creating drafts.")
    if len(item["slides"]) > 100:
        raise ImportInputError("A draft can contain at most 100 slides. Split this file.")
    for index, slide in enumerate(item["slides"], 1):
        if len(slide["header"]) > 200 or len(slide["body"]) > 10000:
            raise ImportInputError(f"Slide {index} exceeds the 200-character heading or 10,000-character body limit. Split or edit the source file.")


def parse_file(filename, content, *, content_format="slides"):
    name = Path(filename).name
    suffix = Path(name).suffix.lower()
    if suffix not in {".md", ".txt"}:
        raise ImportInputError("Choose a .md or .txt file.")
    if len(content) > MAX_FILE_BYTES:
        raise ImportInputError("Files must be at most 1 MB.")
    try:
        text = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise ImportInputError("Save this file as UTF-8 text.") from None
    if any(ord(char) < 32 and char not in "\t\n" for char in text) or text.startswith(("%PDF-", "{\\rtf")):
        raise ImportInputError("This file is not supported plain text. Export it as UTF-8 Markdown or text.")
    if content_format == "documents":
        return _document_plan(name, text, suffix)
    title = Path(name).stem
    slides = []
    if suffix == ".txt":
        slides = [{"header": title, "body": text.strip()}]
    else:
        body, heading, fence, found_title = [], None, None, False

        def finish():
            if heading is not None or "\n".join(body).strip():
                slides.append({"header": heading if heading is not None else "Introduction",
                               "body": "\n".join(body).strip()})

        for line in text.splitlines():
            match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if fence:
                if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                    fence = None
                body.append(line)
                continue
            if match:
                fence = match[1]
                body.append(line)
                continue
            h1 = re.match(r"^ {0,3}#(?:[ \t]+(.*)|[ \t]*)$", line)
            h2 = re.match(r"^ {0,3}##(?:[ \t]+(.*)|[ \t]*)$", line)
            if h1 and not found_title:
                title = re.sub(r"[ \t]+#+[ \t]*$", "", h1[1] or "").strip()
                found_title = True
            elif h2:
                finish()
                heading, body = re.sub(r"[ \t]+#+[ \t]*$", "", h2[1] or "").strip(), []
            else:
                body.append(line)
        finish()
        if not slides:
            slides = [{"header": title, "body": ""}]
        elif len(slides) == 1 and heading is None:
            slides[0]["header"] = title
    item = {"id": str(uuid.uuid4()), "filename": name, "title": title, "slides": slides}
    item["warnings"] = review_warnings(item)
    # A long filename/H1 can be corrected on review. Structural overflow needs a new source file.
    validate_plan_item({**item, "title": ""})
    return item


def new_batch(owner, plan, *, now=None):
    now = now or timezone.now()
    return ImportBatch.objects.create(owner=owner, plan=plan, expires_at=now + timedelta(hours=1))


def update_batch(*, batch_id, owner, revision, titles, remove=None, commit=False, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        batch = ImportBatch.objects.select_for_update().get(pk=batch_id, owner=owner)
        if not owner.is_demo or not owner.is_active or owner.role != owner.Role.ADMINISTRATOR:
            raise ImportInputError("Administrator access is required.")
        if batch.committed_at:
            return batch
        if batch.expires_at <= now:
            raise ImportInputError("This review expired. Select the files again.")
        if revision != batch.revision:
            raise ImportInputError("This review changed. Reload it before submitting again.")
        if remove and remove not in {item["id"] for item in batch.plan}:
            raise ImportInputError("Choose a file in this review.")
        plan = [{**item, "title": titles.get(item["id"], item["title"])}
                for item in batch.plan if item["id"] != remove]
        for item in plan:
            validate_plan_item(item)
            item["warnings"] = review_warnings(item)
        if commit and not plan:
            raise ImportInputError("Keep at least one file to create drafts.")
        batch.plan = plan
        batch.revision += 1
        if commit:
            results = []
            for item in plan:
                if item.get("content_format") == "documents":
                    from .documents import edit_outline
                    training = create_training(content_format="documents")
                    version = training.versions.get()
                    for index, document in enumerate(item["documents"], 1):
                        OutlineItem.objects.create(version=version, kind="document", position=index,
                            title=item["title"], body=document["body"])
                    edit_outline(version_id=version.pk, revision=0, action="settings", training_title=item["title"])
                    results.append(str(training.pk))
                    continue
                training = create_training()
                version = training.versions.get()
                # Establish slide IDs, then use the same validated save service as the editor.
                slides = [Slide(version=version, position=index, **slide) for index, slide in enumerate(item["slides"], 1)]
                Slide.objects.bulk_create(slides)
                save_draft(version_id=version.pk, expected_revision=0, title=item["title"], description="",
                           rows=[{"id": slide.pk, "header": slide.header, "body": slide.body, "footer": ""}
                                 for slide in slides], action="save")
                results.append(str(training.pk))
            batch.result_ids, batch.committed_at = results, now
        batch.save(update_fields=["plan", "revision", "result_ids", "committed_at"])
        return batch


def _document_plan(name, text, suffix):
    from .documents import plain_body
    title, blocks, pending = Path(name).stem, [], []
    fence, found_title = None, False
    def flush():
        if pending:
            blocks.append(plain_body("\n".join(pending))["content"][0])
            pending.clear()
    for line in text.split("\n"):
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line) if suffix == ".md" else None
        if fence:
            pending.append(line)
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
            continue
        if match:
            fence = match[1]
            pending.append(line)
            continue
        heading = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$", line) if suffix == ".md" else None
        if heading:
            value = re.sub(r"[ \t]+#+[ \t]*$", "", heading[2] or "").strip()
            if len(heading[1]) == 1 and not found_title:
                title, found_title = value, True
                continue
            flush()
            blocks.append({"type": "heading", "attrs": {"level": min(3, max(1, len(heading[1])-1))},
                           **({"content": [{"type": "text", "text": value}]} if value else {})})
        else:
            pending.append(line)
    flush()
    item = {"id": str(uuid.uuid4()), "filename": name, "title": title, "content_format": "documents",
            "documents": [{"body": {"type": "doc", "content": blocks or plain_body("")["content"]}}]}
    validate_plan_item({**item, "title": ""})
    item["warnings"] = review_warnings(item)
    return item
