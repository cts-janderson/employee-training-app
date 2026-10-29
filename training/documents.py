"""Version-owned outlines and validated rich text, shared by all content consumers."""
import copy
import json
import uuid
from collections import defaultdict
from urllib.parse import urlsplit

from django.db import transaction
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from .models import OutlineItem, TrainingVersion
from .services import DraftConflict, DraftInputError, Problem, _locked_draft

MAX_ITEMS, MAX_DEPTH, MAX_BODY_BYTES, MAX_NODES, MAX_BODY_DEPTH = 250, 8, 100000, 2000, 12
BLOCKS = {"paragraph", "heading", "bullet_list", "ordered_list", "blockquote", "muted"}


def plain_body(text, *, footer=""):
    # A single text node preserves all legacy whitespace and text values.
    def block(kind, value):
        return {"type": kind, **({"content": [{"type": "text", "text": value}]} if value else {})}
    return {"type": "doc", "content": [block("paragraph", text), *([block("muted", footer)] if footer else [])]}


def safe_link(value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 32 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def validate_body(body):
    try:
        if len(json.dumps(body, ensure_ascii=False).encode()) > MAX_BODY_BYTES:
            raise DraftInputError("Document content exceeds 100,000 bytes. Split this document.")
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise DraftInputError("Document content is invalid.") from None
    count = 0
    def visit(node, allowed, depth):
        nonlocal count
        count += 1
        if count > MAX_NODES or depth > MAX_BODY_DEPTH:
            raise DraftInputError("Document content is too deeply nested or has too many blocks. Split this document.")
        if not isinstance(node, dict) or set(node) - {"type", "attrs", "content", "text", "marks"}:
            raise DraftInputError("Document contains unsupported content.")
        kind = node.get("type")
        if not isinstance(kind, str) or kind not in allowed:
            raise DraftInputError("Document contains an unsupported block or invalid nesting.")
        attrs = node.get("attrs", {})
        if not isinstance(attrs, dict):
            raise DraftInputError("Document attributes are invalid.")
        if kind == "heading":
            if set(attrs) - {"level"} or type(attrs.get("level", 1)) is not int or attrs.get("level", 1) not in {1, 2, 3}:
                raise DraftInputError("Choose a heading level from 1 to 3.")
        elif kind == "ordered_list":
            if set(attrs) - {"order"} or type(attrs.get("order", 1)) is not int or not 1 <= attrs.get("order", 1) <= 10000:
                raise DraftInputError("The numbered list start is invalid.")
        elif attrs:
            raise DraftInputError("Document contains unsupported attributes.")
        if kind == "text":
            if not isinstance(node.get("text"), str) or not node["text"] or "content" in node:
                raise DraftInputError("Document text is invalid.")
            marks = node.get("marks", [])
            if not isinstance(marks, list) or len(marks) > 3:
                raise DraftInputError("Document formatting is invalid.")
            seen = set()
            for mark in marks:
                if not isinstance(mark, dict) or set(mark) - {"type", "attrs"}:
                    raise DraftInputError("Document formatting is invalid.")
                name, properties = mark.get("type"), mark.get("attrs", {})
                if not isinstance(name, str) or name not in {"strong", "em", "link"} or name in seen or not isinstance(properties, dict):
                    raise DraftInputError("Document formatting is unsupported.")
                seen.add(name)
                if name == "link":
                    if set(properties) != {"href"} or not safe_link(properties["href"]):
                        raise DraftInputError("Document links must use a valid https:// address.")
                elif properties:
                    raise DraftInputError("Document formatting attributes are unsupported.")
            return
        if "text" in node or "marks" in node:
            raise DraftInputError("Only document text can have inline formatting.")
        children = node.get("content", [])
        if not isinstance(children, list):
            raise DraftInputError("Document block content is invalid.")
        child_types = ({"text", "hard_break"} if kind in {"paragraph", "heading", "muted"}
                       else {"list_item"} if kind in {"bullet_list", "ordered_list"} else BLOCKS)
        if kind == "hard_break":
            if children:
                raise DraftInputError("Line breaks cannot contain content.")
            return
        if kind in {"doc", "list_item", "blockquote", "bullet_list", "ordered_list"} and not children:
            raise DraftInputError("Container blocks need content.")
        if kind == "list_item" and (not isinstance(children[0], dict) or children[0].get("type") != "paragraph"):
            raise DraftInputError("List items must start with a paragraph.")
        for child in children:
            visit(child, child_types, depth + 1)
    visit(body, {"doc"}, 0)
    return body


def body_text(body):
    return body.get("text", "") + "".join(body_text(child) for child in body.get("content", []))


def render_body(body):
    validate_body(body)
    def render(node):
        kind = node["type"]
        if kind == "text":
            value = str(escape(node["text"]))
            for mark in node.get("marks", []):
                if mark["type"] == "link":
                    value = str(format_html('<a href="{}" rel="noopener noreferrer">{}</a>', mark["attrs"]["href"], mark_safe(value)))
                else:
                    tag = "strong" if mark["type"] == "strong" else "em"
                    value = f"<{tag}>{value}</{tag}>"
            return value
        content = "".join(render(child) for child in node.get("content", []))
        if kind == "doc":
            return content
        if kind == "hard_break":
            return "<br>"
        tag = {"paragraph": "p", "heading": f'h{node.get("attrs", {}).get("level", 1) + 1}',
               "bullet_list": "ul", "ordered_list": "ol", "list_item": "li", "blockquote": "blockquote", "muted": "p"}[kind]
        attrs = ' class="muted"' if kind == "muted" else f' start="{node.get("attrs", {}).get("order", 1)}"' if kind == "ordered_list" else ""
        return f"<{tag}{attrs}>{content}</{tag}>"
    return mark_safe(render(body))


def outline(version):
    items = list(version.outline_items.all())
    by_id = {item.pk: item for item in items}
    children = defaultdict(list)
    for item in items:
        children[item.parent_id].append(item)
    rows, seen = [], set()
    def walk(parent, depth, path):
        if not children[parent]:
            return
        if depth > MAX_DEPTH:
            raise DraftInputError("The outline exceeds eight levels. Move items closer to the root.")
        for item in sorted(children[parent], key=lambda item: (item.position, item.pk)):
            if item.pk in seen:
                raise DraftInputError("The outline contains a cycle.")
            seen.add(item.pk)
            item.depth, item.path = depth, " / ".join([*path, item.title or "Untitled"])
            rows.append(item)
            walk(item.pk, depth + 1, [*path, item.title or "Untitled"])
    if len(items) > MAX_ITEMS:
        raise DraftInputError("A training may contain at most 250 outline items.")
    for item in items:
        parent = by_id.get(item.parent_id)
        if item.parent_id and (parent is None or parent.kind != "section"):
            raise DraftInputError("Outline parents must be sections in the same version.")
    walk(None, 1, [])
    if len(seen) != len(items):
        raise DraftInputError("The outline contains a cycle or unreachable item.")
    return rows


def documents(version):
    return [item for item in outline(version) if item.kind == "document"]


def publication_problems(version):
    problems = []
    if not version.title.strip():
        problems.append(Problem("Add a training title.", ("title",)))
    try:
        rows = outline(version)
        if not any(item.kind == "document" for item in rows):
            problems.append("Add at least one document.")
        for item in rows:
            if not item.title.strip():
                problems.append(Problem(f"{item.path}: add a title.", ("document", str(item.pk))))
            if item.kind == "document":
                validate_body(item.body)
                if not body_text(item.body).strip():
                    problems.append(Problem(f"{item.path}: add document content.", ("document", str(item.pk))))
    except DraftInputError as exc:
        problems.append(str(exc))
    return problems


def _uuid(value):
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, TypeError, AttributeError):
        raise DraftInputError("Choose a valid outline item.") from None


def descendants(rows, target):
    ids = {target}
    for item in rows:
        if item.parent_id in ids:
            ids.add(item.pk)
    return [item for item in rows if item.pk in ids]


@transaction.atomic
def edit_outline(*, version_id, revision, action, target=None, parent=None, position=None,
                 title=None, body=None, training_title=None, description=None, confirm=False):
    version = _locked_draft(version_id, revision, stale_message="A newer draft was saved. Your changes were not saved; reload before trying again.")
    if version.content_format != "documents":
        raise DraftInputError("Open the slide editor for this version.")
    rows = outline(version)
    by_id = {item.pk: item for item in rows}
    target_id, parent_id = _uuid(target), _uuid(parent)
    item = by_id.get(target_id)
    if target_id and item is None:
        raise DraftInputError("The selected item is not in this draft.")
    if title is not None and (not isinstance(title, str) or len(title) > 200):
        raise DraftInputError("Outline titles must be at most 200 characters.")
    if training_title is not None:
        if len(training_title) > 200 or len(description or "") > 5000:
            raise DraftInputError("The training title or description is too long.")
        version.title, version.description = training_title, description or ""
    if item is not None:
        if title is not None:
            item.title = title
        if item.kind == "document" and body is not None:
            item.body = validate_body(body)
    siblings = defaultdict(list)
    for row in rows:
        siblings[row.parent_id].append(row)
    added, removed, selected = [], [], item
    if action in {"add_document", "add_section"}:
        if parent_id and (parent_id not in by_id or by_id[parent_id].kind != "section"):
            raise DraftInputError("Choose a section in this draft.")
        selected = OutlineItem(version=version, parent_id=parent_id, kind=action[4:],
            position=len(siblings[parent_id]) + 1, title=("Untitled document" if action == "add_document" else "Untitled section"), body=plain_body("") if action == "add_document" else {})
        siblings[parent_id].append(selected)
        added.append(selected)
    elif action == "settings":
        pass
    elif item is None:
        raise DraftInputError("Choose an outline item.")
    elif action == "save":
        if title is not None:
            item.title = title
        if item.kind == "document" and body is not None:
            item.body = validate_body(body)
    elif action == "move":
        affected = {row.pk for row in descendants(rows, item.pk)}
        if parent_id in affected or (parent_id and (parent_id not in by_id or by_id[parent_id].kind != "section")):
            raise DraftInputError("Move into a section in this draft, outside this item's descendants.")
        try:
            insertion = int(position)
        except (ValueError, TypeError):
            raise DraftInputError("Choose a destination position.") from None
        siblings[item.parent_id].remove(item)
        if not 1 <= insertion <= len(siblings[parent_id]) + 1:
            raise DraftInputError("Choose a valid destination position.")
        item.parent_id = parent_id
        siblings[parent_id].insert(insertion - 1, item)
    elif action == "duplicate":
        copies = {}
        for old in descendants(rows, item.pk):
            new = OutlineItem(version=version, parent_id=copies[old.parent_id].pk if old.parent_id in copies else old.parent_id,
                kind=old.kind, position=old.position, title=old.title, body=copy.deepcopy(old.body))
            copies[old.pk] = new
            added.append(new)
            if old.pk == item.pk:
                siblings[old.parent_id].insert(siblings[old.parent_id].index(item) + 1, new)
                selected = new
            else:
                siblings[new.parent_id].append(new)
    elif action == "remove":
        if not confirm:
            raise DraftInputError("Review removal and confirm the affected descendants first.")
        removed = descendants(rows, item.pk)
        for old in removed:
            siblings[old.parent_id].remove(old)
        selected = None
    else:
        raise DraftInputError("Choose a supported document action.")
    survivors = [row for group in siblings.values() for row in group]
    if len(survivors) > MAX_ITEMS:
        raise DraftInputError("A training may contain at most 250 outline items.")
    # Move existing positions outside the live range before assigning dense order.
    for index, row in enumerate(rows, 1000):
        OutlineItem.objects.filter(pk=row.pk).update(position=index)
    for old in reversed(removed):
        old.delete()
    for group in siblings.values():
        for index, row in enumerate(group, 1):
            row.position = index
            if row in added:
                row.save(force_insert=True)
            else:
                row.save()
    outline(version)  # Depth and parent validation happens inside the transaction.
    version.revision += 1
    version.save(update_fields=["title", "description", "revision"])
    return version, selected


def copy_into(source, draft):
    """Copy an outline or map slides into a *new* draft; never touch source evidence."""
    if source.content_format == "documents":
        copies = {}
        for old in outline(source):
            new = OutlineItem.objects.create(version=draft, parent=copies.get(old.parent_id),
                kind=old.kind, position=old.position, title=old.title, body=copy.deepcopy(old.body))
            copies[old.pk] = new
    else:
        for slide in source.slides.all():
            OutlineItem.objects.create(version=draft, kind="document", position=slide.position,
                title=slide.header, body=plain_body(slide.body, footer=slide.footer))


@transaction.atomic
def convert_draft(version_id, revision):
    version = _locked_draft(version_id, revision, stale_message="The draft changed. Review it again before conversion.")
    if version.content_format == "documents":
        return version
    copy_into(version, version)
    version.slides.all().delete()
    version.content_format = "documents"
    version.revision += 1
    version.save(update_fields=["content_format", "revision"])
    return version
