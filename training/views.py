import uuid
from urllib.parse import urlencode

from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .authoring_ui import draft_errors, record_save, saved_slide_rows, submitted_slide_rows
from .browsing import browse, safe_return_to
from .dev_auth import demo_actor_required, demo_admin_required
from .models import Employee, Quiz, Training, TrainingVersion
from .services import (
    DraftConflict,
    DraftInputError,
    PublicationBlocked,
    Problem,
    create_revision,
    create_training,
    publication_problems,
    publish_saved_draft,
    read_slide_rows,
    read_material_rows,
    save_draft,
    save_materials,
)
from .quiz_views import editable_version


@require_GET
@demo_actor_required
def home(request):
    return redirect("training_list" if request.employee.role == Employee.Role.ADMINISTRATOR else "my_training")


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


@require_GET
@demo_admin_required
def training_list(request):
    status_filter = request.GET.get("status", "")
    if status_filter not in {"draft", "published"}:
        status_filter = ""
    sort = request.GET.get("sort", "title")
    if sort not in {"title", "-title"}:
        sort = "title"
    version_query = TrainingVersion.objects.annotate(
        slide_count=Count("slides", distinct=True),
        document_count=Count("outline_items", filter=Q(outline_items__kind="document"), distinct=True),
        has_quiz=Exists(Quiz.objects.filter(version_id=OuterRef("pk"))),
    ).order_by("version_number")
    trainings = Training.objects.with_display_title().prefetch_related(
        Prefetch("versions", queryset=version_query),
    ).order_by(Lower("display_title").desc() if sort == "-title" else Lower("display_title"), "id")
    if status_filter:
        trainings = trainings.filter(versions__status=status_filter).distinct()
    listing = browse(request, trainings, search_fields=("versions__title",),
                     extra_params={"status": status_filter, "sort": sort})
    rows = []
    for training in listing.page.object_list:
        versions = list(training.versions.all())
        draft = next((version for version in versions if version.status == TrainingVersion.Status.DRAFT), None)
        published = [version for version in versions if version.status == TrainingVersion.Status.PUBLISHED]
        latest = published[-1] if published else None
        display = latest or draft
        rows.append({
            "training": training,
            "draft": draft,
            "published": published,
            "latest": latest,
            "display_title": training.display_title,
            "display_description": display.description if display else "",
            "draft_title": (draft.title if draft and latest and draft.title != latest.title else ""),
        })
    return render(request, "training/list.html", {
        "rows": rows, "browse": listing, "status_filter": status_filter, "sort": sort,
    })


@require_POST
@demo_admin_required
def new_training(request):
    try:
        training = create_training(content_format=request.POST.get("content_format", "slides"))
    except DraftInputError as exc:
        return HttpResponseBadRequest(str(exc))
    return redirect("draft_editor", training_id=training.id)


@require_GET
@demo_admin_required
def create_content(request):
    return render(request, "training/create_content.html")


@require_POST
@demo_admin_required
def new_revision(request, training_id):
    get_object_or_404(Training, pk=training_id)
    try:
        create_revision(training_id, content_format=request.POST.get("content_format") or None)
    except (DraftConflict, DraftInputError) as error:
        return HttpResponseBadRequest(str(error))
    return redirect(reverse("draft_editor", args=[training_id]) + "?" + urlencode({
        "return_to": safe_return_to(request, "training_list"),
    }))


@require_http_methods(["GET", "POST"])
@demo_admin_required
def draft_editor(request, training_id):
    version = editable_version(training_id, for_post=request.method == "POST")
    if version.content_format == "documents":
        from .document_views import document_editor
        return document_editor(request, version)
    if request.method == "GET":
        return render(request, "training/editor.html", {
            "version": version, "slides": saved_slide_rows(version),
            "form_title": version.title, "form_description": version.description,
            "saved_notice": request.session.pop(f"draft_saved_{version.id}", None),
            "back_url": safe_return_to(request, "training_list"),
        })

    submitted = submitted_slide_rows(request.POST)
    title = request.POST.get("title", "")
    description = request.POST.get("description", "")
    action = request.POST.get("action", "")
    try:
        expected_revision = int(request.POST["revision"])
    except (KeyError, ValueError):
        expected_revision = None
    if version.status != TrainingVersion.Status.DRAFT or expected_revision != version.revision:
        return _draft_conflict(request, version, submitted, title, description)
    if action == "cancel_remove":
        return render(request, "training/editor.html", {
            "version": version, "slides": submitted,
            "form_title": title, "form_description": description,
            "form_revision": expected_revision,
        })
    if action.startswith("review_remove:"):
        target = action.partition(":")[2]
        if target in {str(slide.id) for slide in version.slides.all()}:
            return render(request, "training/editor.html", {
                "version": version, "slides": submitted,
                "form_title": title, "form_description": description,
                "form_revision": expected_revision,
                "pending_remove": {
                    "label": next((slide["header"] for slide in submitted if slide["id"] == target), "") or "this slide",
                    "action": f"remove:{target}",
                },
            })
    try:
        rows = read_slide_rows(request.POST)
        version, _ = save_draft(
            version_id=version.id,
            expected_revision=expected_revision,
            title=title,
            description=description,
            rows=rows,
            action=action,
        )
    except (KeyError, ValueError, DraftInputError) as error:
        links, title_error, description_error = draft_errors(
            [Problem(str(error), getattr(error, "target", None))], submitted, training_id,
            title=title, description=description,
        )
        return render(
            request, "training/editor.html",
            {
                "version": version, "slides": submitted, "errors": links,
                "title_error": title_error, "description_error": description_error,
                "form_title": title,
                "form_description": description, "form_revision": expected_revision,
            },
            status=400,
        )
    except DraftConflict:
        return _draft_conflict(request, version, submitted, title, description)
    record_save(request, f"draft_saved_{version.id}", version.revision)
    return redirect("draft_editor", training_id=training_id)


def _publication_review_page(request, version, *, conflict=False, blocked=None, status=200):
    from django.urls import reverse

    if version.content_format == "documents":
        from .documents import documents
        slides = documents(version)
    else:
        slides = list(version.slides.all())
    quiz = Quiz.objects.filter(version=version).prefetch_related("questions__options").first()
    questions = list(quiz.questions.all()) if quiz else []
    problems = blocked if blocked is not None else publication_problems(version)
    links, _, _ = draft_errors(problems, saved_slide_rows(version), version.training_id, title=version.title)
    editor_url = reverse("draft_editor", args=[version.training_id])
    groups = []
    for item in links:
        label = ("Supporting material" if "#supporting-material" in item["href"] else
                 item["message"].split(":", 1)[0] if ":" in item["message"] else "Training")
        href = item["href"]
        if (getattr(item["message"], "target", None) or (None,))[0] == "document":
            href = editor_url + "?item=" + item["message"].target[1]
        if href.startswith("#"):
            href = editor_url + href
        if not groups or groups[-1]["label"] != label:
            groups.append({"label": label, "items": []})
        groups[-1]["items"].append({"message": item["message"], "href": href})
    return render(request, "training/publication_review.html", {
        "version": version, "slides": slides, "slide_count": len(slides),
        "quiz": quiz, "questions": questions, "question_count": len(questions),
        "blocker_groups": groups, "conflict": conflict,
        "published_conflict": conflict and version.status == TrainingVersion.Status.PUBLISHED,
    }, status=status)


@require_http_methods(["GET", "POST"])
@demo_admin_required
def publication_review(request, training_id):
    get_object_or_404(Training, pk=training_id)
    if request.method == "GET":
        version = TrainingVersion.objects.filter(
            training_id=training_id, status=TrainingVersion.Status.DRAFT,
        ).first()
        if version is None:
            latest = TrainingVersion.objects.filter(training_id=training_id).order_by("-version_number").first()
            if latest is None:
                raise Http404("Training version not found.")
            return redirect("preview", training_id=training_id, version_id=latest.id)
        return _publication_review_page(request, version)

    try:
        version_id = uuid.UUID(request.POST["version_id"])
    except (KeyError, ValueError, AttributeError):
        return HttpResponseBadRequest("The reviewed version is missing. Open the publication review again.")
    version = get_object_or_404(TrainingVersion, pk=version_id, training_id=training_id)
    try:
        expected_revision = int(request.POST["revision"])
    except (KeyError, ValueError):
        return HttpResponseBadRequest("The reviewed revision is missing. Open the publication review again.")
    try:
        published = publish_saved_draft(version_id=version.id, expected_revision=expected_revision)
    except DraftConflict:
        version.refresh_from_db()
        return _publication_review_page(request, version, conflict=True, status=409)
    except PublicationBlocked as error:
        version.refresh_from_db()
        return _publication_review_page(request, version, blocked=error.problems, status=409)
    return redirect("preview", training_id=training_id, version_id=published.id)


def _draft_conflict(request, version, submitted, title, description):
    version.refresh_from_db()
    return render(request, "training/editor.html", {
        "version": version, "slides": submitted,
        "form_title": title, "form_description": description,
        "form_revision": version.revision,
        "conflict": True,
        "published_conflict": version.status != TrainingVersion.Status.DRAFT,
        "server_slides": saved_slide_rows(version),
    }, status=409)


@require_GET
@demo_admin_required
def preview(request, training_id, version_id):
    version = get_object_or_404(TrainingVersion, pk=version_id, training_id=training_id)
    quiz = Quiz.objects.filter(version=version).prefetch_related("questions__options").first()
    if version.content_format == "documents":
        from .documents import documents
        return render(request, "training/document_preview.html", {"version": version, "documents": documents(version), "quiz": quiz})
    return render(request, "training/preview.html", {"version": version, "slides": version.slides.all(), "quiz": quiz})


@require_POST
@demo_admin_required
def save_materials_view(request, training_id):
    version = get_object_or_404(TrainingVersion, training_id=training_id, status=TrainingVersion.Status.DRAFT)
    try:
        expected_revision = int(request.POST["revision"])
        rows = read_material_rows(request.POST)
        saved = save_materials(version_id=version.id, expected_revision=expected_revision,
                               rows=rows, action=request.POST.get("action", "save"))
    except (KeyError, ValueError, DraftInputError, DraftConflict) as exc:
        version.refresh_from_db()
        if version.content_format == "documents":
            from .document_views import document_editor
            submitted_materials = [
                {"id": item_id, "name": name, "url": url, "description": description}
                for item_id, name, url, description in zip(request.POST.getlist("material_id"),
                    request.POST.getlist("material_name"), request.POST.getlist("material_url"),
                    request.POST.getlist("material_description"))]
            return document_editor(request, version, material_error=str(exc), material_rows=submitted_materials,
                material_status=409 if isinstance(exc, DraftConflict) else 400)
        return render(request, "training/editor.html", {
            "version": version, "slides": saved_slide_rows(version),
            "form_title": version.title, "form_description": version.description,
            "material_rows": request.POST.getlist("material_id") and [
                {"id": item_id, "name": name, "url": url, "description": description}
                for item_id, name, url, description in zip(
                    request.POST.getlist("material_id"), request.POST.getlist("material_name"),
                    request.POST.getlist("material_url"), request.POST.getlist("material_description"),
                )
            ],
            "material_error": str(exc),
        }, status=409 if isinstance(exc, DraftConflict) else 400)
    record_save(request, f"draft_saved_{version.id}", saved.revision)
    return redirect("draft_editor", training_id=training_id)
