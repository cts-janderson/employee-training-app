import uuid

from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from .authoring_ui import (
    quiz_errors, quiz_focus_target, quiz_workspace, record_save, saved_question_rows, submitted_question_rows,
)
from .dev_auth import admin_required
from .models import Quiz, TrainingVersion
from .quiz_authoring import read_question_rows, removal_review, save_quiz_draft
from .services import DraftConflict, DraftInputError, Problem


def editable_version(training_id, *, for_post):
    version = TrainingVersion.objects.filter(
        training_id=training_id, status=TrainingVersion.Status.DRAFT,
    ).first()
    if version is not None:
        return version
    if not for_post:
        raise Http404("Draft not found.")
    version = TrainingVersion.objects.filter(training_id=training_id).order_by("-version_number").first()
    if version is None:
        raise Http404("Training version not found.")
    return version


@require_http_methods(["GET", "POST"])
@admin_required
def quiz_editor(request, training_id):
    version = editable_version(training_id, for_post=request.method == "POST")
    quiz = Quiz.objects.filter(version=version).prefetch_related("questions__options").first()
    if request.method == "GET":
        return render(request, "training/quiz_editor.html", {
            **quiz_workspace(quiz),
            "version": version, "quiz_present": quiz is not None,
            "quiz_id": quiz.id if quiz else None,
            "questions": saved_question_rows(quiz),
            "threshold_value": quiz.passing_percent if quiz and quiz.passing_percent is not None else "",
            "saved_notice": request.session.pop(f"quiz_saved_{version.id}", None),
        })
    action = request.POST.get("action", "save")
    submitted = submitted_question_rows(request.POST)
    threshold = request.POST.get("passing_percent", "")
    try:
        expected_revision = int(request.POST["revision"])
    except (KeyError, ValueError):
        expected_revision = None
    if version.status != TrainingVersion.Status.DRAFT or expected_revision != version.revision:
        return _quiz_conflict(request, version, submitted, threshold)
    if action == "cancel_remove":
        return _quiz_response(request, version, quiz, submitted, threshold, expected_revision)
    if action.startswith("review_remove"):
        pending = removal_review(quiz, action)
        if pending:
            return _quiz_response(request, version, quiz, submitted, threshold, expected_revision, pending_remove=pending)
    try:
        rows = [] if action in {"add_quiz", "remove_quiz"} else read_question_rows(request.POST)
        saved_version = save_quiz_draft(
            version_id=version.id,
            expected_revision=expected_revision,
            threshold_value=threshold,
            rows=rows, action=action,
        )
        record_save(request, f"quiz_saved_{version.id}", saved_version.revision)
        response = redirect("quiz_editor", training_id=training_id)
        focus = quiz_focus_target(Quiz.objects.filter(version=version).first(), action, before=quiz)
        if focus:
            response["Location"] += f"#{focus}"
        return response
    except (DraftInputError, KeyError, ValueError) as exc:
        return _quiz_response(
            request, version, quiz, submitted, threshold, expected_revision,
            messages=[Problem(str(exc), getattr(exc, "target", None))], status=400,
        )
    except DraftConflict:
        return _quiz_conflict(request, version, submitted, threshold)


def _quiz_response(request, version, quiz, questions, threshold, revision, *,
                   messages=None, status=200, pending_remove=None, conflict=False,
                   server_questions=None, server_threshold=None):
    links, threshold_error = quiz_errors(messages or [], questions)
    return render(request, "training/quiz_editor.html", {
        **quiz_workspace(quiz),
        "version": version, "quiz_present": quiz is not None or bool(questions) or "passing_percent" in request.POST,
        "quiz_id": quiz.id if quiz else None,
        "questions": questions, "threshold_value": threshold,
        "form_revision": revision, "errors": links, "threshold_error": threshold_error,
        "pending_remove": pending_remove, "conflict": conflict,
        "published_conflict": conflict and version.status != TrainingVersion.Status.DRAFT,
        "server_questions": server_questions, "server_threshold": server_threshold,
    }, status=status)


def _quiz_conflict(request, version, submitted, threshold):
    version.refresh_from_db()
    server_quiz = Quiz.objects.filter(version=version).prefetch_related("questions__options").first()
    return _quiz_response(
        request, version, server_quiz, submitted, threshold, version.revision,
        status=409, conflict=True, server_questions=saved_question_rows(server_quiz),
        server_threshold=server_quiz.passing_percent if server_quiz else None,
    )


@require_GET
@admin_required
def quiz_learner_preview(request, quiz_id):
    from .quiz_learner_views import _question_rows

    quiz = get_object_or_404(Quiz.objects.select_related("version").prefetch_related("questions__options"), pk=quiz_id)
    return render(request, "training/quiz_learner_preview.html", {
        "quiz": quiz, "questions": _question_rows(quiz, uuid.uuid4()),
    })
