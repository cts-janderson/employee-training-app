import uuid

from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .authoring_ui import quiz_errors, quiz_focus_target, quiz_workspace, record_save, saved_question_rows, submitted_question_rows
from .browsing import browse, safe_return_to
from .dev_auth import demo_admin_required
from .models import Quiz, TrainingVersion
from .quiz_authoring import library_quiz_problems, read_question_rows, removal_review, save_library_quiz
from .services import DraftConflict, DraftInputError, Problem, attach_library_quiz


@require_GET
@demo_admin_required
def library_quiz_list(request):
    quizzes = Quiz.objects.filter(version__isnull=True).prefetch_related("questions__options").order_by("title", "id")
    listing = browse(request, quizzes, search_fields=("title",))
    rows = [{"quiz": quiz, "problems": library_quiz_problems(quiz),
             "question_count": quiz.questions.count()} for quiz in listing.page.object_list]
    drafts = TrainingVersion.objects.filter(status=TrainingVersion.Status.DRAFT).order_by("title", "version_number")
    return render(request, "training/library_quiz_list.html", {"rows": rows, "drafts": drafts,
                                                                 "browse": listing})


@require_POST
@demo_admin_required
def new_library_quiz(request):
    quiz = Quiz.objects.create()
    return redirect("library_quiz_editor", quiz_id=quiz.id)


def _render_library_editor(request, quiz, questions, threshold, title, *, messages=None,
                           pending_remove=None, conflict=False, status=200, saved_notice=None):
    links, threshold_error = quiz_errors(messages or [], questions)
    title_error = next((message for message in messages or [] if getattr(message, "target", None) == ("title",)), "")
    if title_error:
        links = [{"message": title_error, "href": "#quiz-title"},
                 *[link for link in links if link["message"] != title_error]]
    return render(request, "training/quiz_editor.html", {
        **quiz_workspace(quiz, library=True),
        "library": True, "version": quiz, "library_quiz": quiz,
        "quiz_id": quiz.id,
        "quiz_present": True, "questions": questions,
        "threshold_value": threshold, "title_value": title,
        "form_revision": quiz.revision, "errors": links,
        "threshold_error": threshold_error, "title_error": title_error,
        "pending_remove": pending_remove, "conflict": conflict,
        "server_questions": saved_question_rows(quiz) if conflict else None,
        "server_threshold": quiz.passing_percent if conflict else None,
        "saved_notice": saved_notice,
        "back_url": safe_return_to(request, "library_quiz_list"),
    }, status=status)


@require_http_methods(["GET", "POST"])
@demo_admin_required
def library_quiz_editor(request, quiz_id):
    quiz = get_object_or_404(Quiz.objects.prefetch_related("questions__options"), pk=quiz_id, version__isnull=True)
    if request.method == "GET":
        return _render_library_editor(
            request, quiz, saved_question_rows(quiz),
            quiz.passing_percent if quiz.passing_percent is not None else "", quiz.title,
            saved_notice=request.session.pop(f"library_quiz_saved_{quiz.id}", None),
        )
    action = request.POST.get("action", "save")
    submitted = submitted_question_rows(request.POST)
    title = request.POST.get("title", "")
    threshold = request.POST.get("passing_percent", "")
    try:
        revision = int(request.POST["revision"])
    except (KeyError, ValueError):
        revision = None
    if revision != quiz.revision:
        return _render_library_editor(request, quiz, submitted, threshold, title, conflict=True, status=409)
    if action == "cancel_remove":
        return _render_library_editor(request, quiz, submitted, threshold, title)
    if action.startswith("review_remove"):
        pending = removal_review(quiz, action, allow_quiz_removal=False)
        if pending:
            return _render_library_editor(request, quiz, submitted, threshold, title, pending_remove=pending)
    try:
        rows = read_question_rows(request.POST)
        saved = save_library_quiz(
            quiz_id=quiz.id, expected_revision=revision, title=title,
            threshold_value=threshold, rows=rows, action=action,
        )
    except (DraftInputError, KeyError, ValueError) as exc:
        return _render_library_editor(request, quiz, submitted, threshold, title,
                                      messages=[Problem(str(exc), getattr(exc, "target", None))], status=400)
    except DraftConflict:
        quiz.refresh_from_db()
        return _render_library_editor(request, quiz, submitted, threshold, title, conflict=True, status=409)
    record_save(request, f"library_quiz_saved_{quiz.id}", saved.revision)
    response = redirect("library_quiz_editor", quiz_id=quiz.id)
    focus = quiz_focus_target(Quiz.objects.filter(pk=quiz.id).first(), action, before=quiz)
    if focus:
        response["Location"] += f"#{focus}"
    return response


def _attachment_context(training_id, version, selected_id=None, *, conflict=False, error=""):
    library = list(Quiz.objects.filter(version__isnull=True).prefetch_related("questions__options").order_by("title", "id"))
    rows = [{"quiz": quiz, "problems": library_quiz_problems(quiz),
             "question_count": quiz.questions.count()} for quiz in library]
    selected = next((row for row in rows if str(row["quiz"].id) == str(selected_id)), None)
    current = Quiz.objects.filter(version=version).prefetch_related("questions__options").first()
    return {
        "training_id": training_id, "version": version, "rows": rows,
        "selected": selected, "selected_questions": saved_question_rows(selected["quiz"]) if selected else [],
        "current": current, "current_questions": saved_question_rows(current),
        "conflict": conflict, "error": error,
    }


@require_http_methods(["GET", "POST"])
@demo_admin_required
def attach_library_quiz_view(request, training_id):
    if request.method == "GET":
        version = TrainingVersion.objects.filter(training_id=training_id, status=TrainingVersion.Status.DRAFT).first()
        if version is None:
            raise Http404("Draft not found.")
        return render(request, "training/quiz_attach.html",
                      _attachment_context(training_id, version, request.GET.get("quiz")))
    try:
        version_id = uuid.UUID(request.POST["version_id"])
        quiz_id = uuid.UUID(request.POST["quiz_id"])
        version_revision = int(request.POST["version_revision"])
        quiz_revision = int(request.POST["quiz_revision"])
    except (KeyError, ValueError, TypeError):
        return HttpResponseBadRequest("Open attachment review and choose a saved library quiz.")
    version = get_object_or_404(TrainingVersion, pk=version_id, training_id=training_id)
    source = get_object_or_404(Quiz, pk=quiz_id, version__isnull=True)
    try:
        attach_library_quiz(
            version_id=version.id, quiz_id=source.id,
            expected_version_revision=version_revision, expected_quiz_revision=quiz_revision,
            confirm=request.POST.get("confirm", ""),
        )
    except DraftConflict:
        version.refresh_from_db()
        return render(request, "training/quiz_attach.html",
                      _attachment_context(training_id, version, quiz_id, conflict=True), status=409)
    except DraftInputError as exc:
        return render(request, "training/quiz_attach.html",
                      _attachment_context(training_id, version, quiz_id, error=str(exc)), status=409)
    return redirect("quiz_editor", training_id=training_id)
