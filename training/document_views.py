import json
from urllib.parse import urlencode

from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .assignments import AssignmentAccessError, learner_access, visit_document
from .authoring_ui import record_save
from .dev_auth import actor_required, admin_required
from .documents import convert_draft, descendants, edit_outline, outline, plain_body
from .learning_content import acknowledged_ids, units
from .models import OutlineItem, TrainingVersion
from .services import DraftConflict, DraftInputError


def document_editor(request, version, *, material_error=None, material_rows=None, material_status=200):
    rows = outline(version)
    selected_id = request.POST.get("target") if request.method == "POST" else request.GET.get("item")
    selected = next((item for item in rows if str(item.pk) == selected_id), None)
    if not selected and (request.method == "GET" or material_error):
        selected = next((item for item in rows if item.kind == "document"), None)
    body = selected.body if selected and selected.kind == "document" else plain_body("")
    error, conflict, pending = "", False, []
    status = material_status
    submitted_title = selected.title if selected else ""
    training_title, description = version.title, version.description
    if request.method == "POST" and not material_error:
        submitted_title = request.POST.get("item_title", submitted_title)
        training_title = request.POST.get("title", training_title)
        description = request.POST.get("description", description)
        raw_body = request.POST.get("body")
        try:
            revision = int(request.POST.get("revision", ""))
        except ValueError:
            revision = None
        try:
            # Conflict has precedence over parsing malformed rich text.
            if revision != version.revision or version.status != "draft":
                raise DraftConflict("A newer draft was saved or this version was published. Your changes were not saved.")
            action = request.POST.get("action", "save")
            if raw_body is not None:
                try:
                    body = json.loads(raw_body)
                except (ValueError, RecursionError):
                    raise DraftInputError("The editor content is invalid. Copy your text before reloading.") from None
            if action == "review_remove":
                if not selected:
                    raise DraftInputError("Choose an item to remove.")
                pending = descendants(rows, selected.pk)
            elif action != "cancel_remove":
                saved, target = edit_outline(version_id=version.pk, revision=revision, action=action,
                    target=request.POST.get("target"), parent=request.POST.get("parent"),
                    position=request.POST.get("position"), title=submitted_title, body=body,
                    training_title=training_title, description=description,
                    confirm=request.POST.get("confirm") == "yes")
                record_save(request, f"draft_saved_{version.pk}", saved.revision)
                return redirect(reverse("draft_editor", args=[version.training_id]) +
                    ("?" + urlencode({"item": target.pk}) if target else ""))
        except DraftConflict as exc:
            error, conflict, status = str(exc), True, 409
            version.refresh_from_db()
        except DraftInputError as exc:
            error, status = str(exc), 400
    return render(request, "training/document_editor.html", {
        "material_error": material_error, "material_rows": material_rows,
        "version": version, "outline": rows, "selected": selected, "editor_body": body,
        "item_title": submitted_title, "form_title": training_title, "form_description": description,
        "body_json": request.POST.get("body", json.dumps(body, ensure_ascii=False)) if request.method == "POST" else json.dumps(body, ensure_ascii=False),
        "error": error, "conflict": conflict, "pending_remove": pending,
        "sections": [item for item in rows if item.kind == "section"],
        "saved_notice": request.session.pop(f"draft_saved_{version.pk}", None),
    }, status=status)


@require_POST
@admin_required
def convert_documents(request, training_id):
    version = get_object_or_404(TrainingVersion, training_id=training_id, status="draft")
    try:
        if request.POST.get("confirm") != "convert":
            raise DraftInputError("Confirm conversion to documents first.")
        convert_draft(version.pk, int(request.POST.get("revision", "")))
    except (ValueError, DraftInputError, DraftConflict) as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("draft_editor", training_id=training_id)


@require_GET
@actor_required
def assignment_document(request, assignment_id, document_id):
    access = learner_access(assignment_id, request.employee)
    if not access.can_review:
        raise Http404("Assignment not found.")
    assignment = access.assignment
    document = get_object_or_404(OutlineItem, pk=document_id, version_id=assignment.version_id, kind="document")
    if access.can_act:
        try:
            assignment, document = visit_document(assignment_id=assignment.pk, document_id=document.pk, employee=request.employee)
        except AssignmentAccessError as exc:
            return HttpResponseForbidden(str(exc))
    rows = units(assignment.version)
    index = next(i for i, item in enumerate(rows) if item.pk == document.pk)
    outline_rows = outline(assignment.version)
    acknowledged = acknowledged_ids(assignment)
    for item in outline_rows:
        if item.kind == "section":
            required = [child.pk for child in descendants(outline_rows, item.pk) if child.kind == "document"]
            item.required_count = len(required)
            item.acknowledged_count = sum(pk in acknowledged for pk in required)
    return render(request, "training/assignment_document.html", {
        "assignment": assignment, "document": document, "outline": outline_rows,
        "document_path": rows[index].path, "acknowledged": document.pk in acknowledged,
        "previous": rows[index-1] if index else None,
        "next": rows[index+1] if index+1 < len(rows) else None,
    })


@require_POST
@actor_required
def acknowledge_document(request, assignment_id, document_id):
    access = learner_access(assignment_id, request.employee)
    if not access.can_review:
        raise Http404("Assignment not found.")
    if not access.can_act:
        return HttpResponseForbidden(access.message)
    document = get_object_or_404(OutlineItem, pk=document_id, version_id=access.assignment.version_id, kind="document")
    try:
        visit_document(assignment_id=assignment_id, document_id=document.pk, employee=request.employee, acknowledge=True)
    except AssignmentAccessError as exc:
        return HttpResponseForbidden(str(exc))
    return redirect("assignment_detail", assignment_id=assignment_id)
