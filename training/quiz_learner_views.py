import uuid

from django.http import Http404, HttpResponseForbidden, HttpResponseNotFound
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone

from .assignments import (
    AssignmentAccessError, LearnerDenial, assignment_status, learner_access, slides_acknowledged,
)
from .dev_auth import demo_actor_required
from .models import Assignment, Quiz, QuizAttempt
from .quizzes import QuizSubmissionError, displayed_score, presentation_options, submit_quiz, submitted_answer_rows


def _question_rows(quiz, submission_id, post=None):
    from .question_types import for_type

    rows = []
    for question in quiz.questions.prefetch_related("options"):
        question_type = for_type(question.type)
        field_name = question_type.answer_field(question.id)
        selected = post.getlist(field_name) if post is not None else []
        options = presentation_options(question, submission_id)
        rows.append({
            "id": question.id, "position": question.position, "prompt": question.prompt,
            "type": question.type, "field_name": field_name,
            "take_template": question_type.take_template, "ranked": question_type.ranked,
            "positions": [{"number": index, "selected": selected[index - 1] if len(selected) >= index else ""}
                          for index in range(1, len(options) + 1)],
            "options": [{"id": option.id, "value": str(option.id), "text": option.text,
                         "selected": str(option.id) in selected}
                        for option in options],
            "selected": selected,
        })
    return rows


def _validate_answers(rows, post):
    from .question_types import for_type

    errors = []
    answers = {}
    expected = {row["field_name"] for row in rows} | {"csrfmiddlewaretoken", "submission_id"}
    if set(post) - expected:
        errors.append({"anchor": "quiz-questions", "message": "The quiz response contains an unexpected field. Review your answers."})
    for row in rows:
        values = row["selected"]
        valid_options = {str(option["id"]) for option in row["options"]}
        anchor = f"question-{row['id']}"
        question_type = for_type(row["type"])
        message = question_type.answer_problem(values, valid_options)
        if message:
            row["error"] = message
            errors.append({"anchor": anchor, "message": f"Question {row['position']}: {message}"})
        else:
            answers[str(row["id"])] = question_type.submitted_answer(values)
    return errors, answers


def _render_quiz(request, assignment, quiz, submission_id):
    return render(request, "training/take_quiz.html", {
        "assignment": assignment, "questions": _question_rows(quiz, submission_id),
        "submission_id": submission_id, "errors": [],
    })


def _unavailable_quiz(request, assignment, message, *, status=409):
    current = Assignment.objects.outstanding().filter(
        employee=request.employee, training=assignment.training,
    ).exclude(pk=assignment.pk).first()
    return render(request, "training/quiz_unavailable.html", {
        "assignment": assignment, "message": message, "current_assignment": current,
    }, status=status)


_CANCELED_BEFORE_SUBMISSION = "This assignment was canceled before your quiz was submitted."


def _refused_submission(request, access):
    """Return the unavailable response for canceled or review-only work, if any."""
    if access.denial == LearnerDenial.CANCELED:
        return _unavailable_quiz(request, access.assignment, _CANCELED_BEFORE_SUBMISSION, status=403)
    if not access.can_act and access.can_review:
        return _unavailable_quiz(request, access.assignment, access.message, status=403)
    return None


@require_GET
@demo_actor_required
def take_quiz(request, assignment_id):
    access = learner_access(
        assignment_id, request.employee, queryset=Assignment.objects.select_related("version"),
    )
    if access.denial in {LearnerDenial.MISSING, LearnerDenial.NOT_YOURS, LearnerDenial.INACTIVE}:
        raise Http404("Assignment not found.")
    assignment = access.assignment
    if not access.can_act:
        return _unavailable_quiz(request, assignment, access.message)
    quiz = get_object_or_404(Quiz, version=assignment.version)
    if assignment.completed_at:
        return redirect("assignment_detail", assignment_id=assignment.id)
    if not slides_acknowledged(assignment):
        return redirect("assignment_detail", assignment_id=assignment.id)
    return _render_quiz(request, assignment, quiz, uuid.uuid4())


@require_POST
@demo_actor_required
def submit_quiz_view(request, assignment_id):
    access = learner_access(
        assignment_id, request.employee, queryset=Assignment.objects.select_related("version"),
    )
    if access.denial == LearnerDenial.MISSING:
        return HttpResponseNotFound("Assignment not found.")
    if access.denial in {LearnerDenial.NOT_YOURS, LearnerDenial.INACTIVE}:
        return HttpResponseForbidden(access.message)
    refused = _refused_submission(request, access)
    if refused is not None:
        return refused
    assignment = access.assignment
    quiz = get_object_or_404(Quiz, version=assignment.version)
    raw_submission_id = request.POST.get("submission_id", "")
    try:
        submission_id = uuid.UUID(raw_submission_id)
    except (ValueError, TypeError, AttributeError):
        submission_id = uuid.uuid4()
        token_error = [{"anchor": "quiz-questions", "message": "The submission expired. Review your answers and submit again."}]
    else:
        token_error = []
    rows = _question_rows(quiz, submission_id, request.POST)
    errors, answers = _validate_answers(rows, request.POST)
    if token_error or errors:
        if assignment.completed_at:
            return _unavailable_quiz(
                request, assignment,
                "This assignment is already complete. Review its current result and attempt history.",
                status=400,
            )
        return render(request, "training/take_quiz.html", {
            "assignment": assignment, "questions": rows, "submission_id": submission_id,
            "errors": token_error + errors,
        }, status=400)
    try:
        attempt = submit_quiz(
            assignment_id=assignment_id, employee=request.employee,
            submission_id=submission_id, answers=answers,
        )
    except AssignmentAccessError as exc:
        current = learner_access(assignment_id, request.employee)
        refused = _refused_submission(request, current)
        if refused is not None:
            return refused
        return HttpResponseForbidden(str(exc))
    except Assignment.DoesNotExist:
        return HttpResponseNotFound("Assignment not found.")
    except QuizSubmissionError as exc:
        assignment.refresh_from_db()
        if assignment.completed_at:
            return _unavailable_quiz(
                request, assignment,
                "This assignment is already complete. Review its current result and attempt history.",
                status=400,
            )
        return render(request, "training/take_quiz.html", {
            "assignment": assignment, "questions": rows, "submission_id": submission_id,
            "errors": [{"anchor": "quiz-questions", "message": str(exc)}],
        }, status=400)
    return redirect("quiz_result", assignment_id=assignment_id, attempt_id=attempt.id)


@require_GET
@demo_actor_required
def quiz_result(request, assignment_id, attempt_id):
    access = learner_access(assignment_id, request.employee)
    if not access.can_review:
        raise Http404("Assignment not found.")
    assignment = access.assignment
    attempt = get_object_or_404(QuizAttempt, pk=attempt_id, assignment=assignment)
    now = timezone.now()
    return render(request, "training/quiz_result.html", {
        "assignment": assignment, "attempt": attempt, "answer_rows": submitted_answer_rows(attempt),
        "score": displayed_score(attempt),
        "deadline": assignment_status(assignment, now=now).deadline_label,
    })
