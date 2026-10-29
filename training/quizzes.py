import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .assignments import _complete_if_satisfied, _owned_assignment, slides_acknowledged
from .models import AttemptResponse, AttemptResponseSelection, Quiz, QuizAttempt
from .notifications import assignment_ended


class QuizSubmissionError(Exception):
    pass


def displayed_score(attempt):
    return (Decimal(attempt.correct_count * 100) / Decimal(attempt.question_count)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP,
    )


@dataclass(frozen=True)
class QuizRecord:
    latest: QuizAttempt | None
    passing: QuizAttempt | None
    outcome: str


def quiz_record(assignment):
    """Summarize attempts in submitted-time and identifier order."""
    attempts = sorted(assignment.quiz_attempts.all(), key=lambda item: (item.submitted_at, str(item.id)))
    latest = attempts[-1] if attempts else None
    passing = next((attempt for attempt in attempts if attempt.passed), None)
    return QuizRecord(latest, passing, "passed" if passing else "retry_needed" if latest else "not_attempted")


def submitted_answer_rows(attempt):
    responses = attempt.responses.select_related("question", "selected_option").prefetch_related(
        "selections__option",
    ).order_by("question__position")
    rows = []
    for response in responses:
        if response.selected_option_id:
            selected_texts = [response.selected_option.text]
        else:
            selected_texts = [selection.option.text for selection in response.selections.all()]
        rows.append({
            "prompt": response.question.prompt,
            "type": response.question.type,
            "selected_texts": selected_texts,
        })
    return rows


def presentation_options(question, submission_id):
    """Sort by attempt-specific option IDs, independently of grading ranks or flags."""
    return sorted(
        question.options.all(),
        key=lambda option: hashlib.sha256(submission_id.bytes + option.id.bytes).digest(),
    )


def _parse_answers(answers):
    if len(answers) > 100:
        raise QuizSubmissionError("The quiz has too many answers.")
    try:
        parsed = {}
        for raw_question_id, raw_answer in answers.items():
            question_id = uuid.UUID(str(raw_question_id))
            if isinstance(raw_answer, (list, tuple)):
                if len(raw_answer) > 20:
                    raise QuizSubmissionError("An answer has too many selections.")
                answer = [uuid.UUID(str(value)) for value in raw_answer]
            else:
                answer = uuid.UUID(str(raw_answer))
            parsed[question_id] = answer
        if len(parsed) != len(answers):
            raise QuizSubmissionError("Answer every question exactly once.")
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise QuizSubmissionError("The submission or an answer is invalid.") from None


def submit_quiz(*, assignment_id, employee, submission_id, answers, now=None):
    from .question_types import for_type

    now = now or timezone.now()
    try:
        submission_id = uuid.UUID(str(submission_id))
    except (ValueError, TypeError, AttributeError):
        raise QuizSubmissionError("The submission ID is invalid.") from None
    answers = _parse_answers(answers)
    with transaction.atomic():
        assignment = _owned_assignment(assignment_id, employee)
        quiz = Quiz.objects.filter(version=assignment.version).first()
        if quiz is None:
            raise QuizSubmissionError("This assignment has no quiz.")
        questions = list(quiz.questions.prefetch_related("options"))
        if not questions or set(answers) != {question.id for question in questions}:
            raise QuizSubmissionError("Answer every question exactly once.")
        normalized = []
        for question in questions:
            options = list(question.options.all())
            by_id = {option.id: option for option in options}
            answer = answers[question.id]
            try:
                question_type = for_type(question.type)
            except KeyError:
                raise QuizSubmissionError("This quiz contains an unsupported question type.")
            selected_ids, correct = question_type.grade(options, answer)
            normalized.append((question, [by_id[value] for value in selected_ids], correct))
        fingerprint = hashlib.sha256(json.dumps(
            [(str(question.id), [str(option.id) for option in options])
             for question, options, _ in normalized], separators=(",", ":"),
        ).encode()).hexdigest()
        existing = QuizAttempt.objects.filter(assignment=assignment, submission_id=submission_id).first()
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise QuizSubmissionError("This submission ID was used with different answers.")
            return existing
        if assignment.completed_at:
            raise QuizSubmissionError("This assignment is already complete.")
        if not slides_acknowledged(assignment):
            raise QuizSubmissionError("Acknowledge every document before submitting the quiz." if assignment.version.content_format == "documents" else "Acknowledge every slide before submitting the quiz.")
        if quiz.passing_percent is None or not 1 <= quiz.passing_percent <= 100:
            raise QuizSubmissionError("This quiz has an invalid passing threshold.")
        correct_count = sum(int(correct) for _, _, correct in normalized)
        passed = correct_count * 100 >= quiz.passing_percent * len(questions)
        attempt = QuizAttempt.objects.create(
            assignment=assignment, quiz=quiz, submission_id=submission_id,
            request_fingerprint=fingerprint, correct_count=correct_count,
            question_count=len(questions), passed=passed, submitted_at=now,
        )
        for question, options, _ in normalized:
            single = for_type(question.type).single_selection
            response = AttemptResponse.objects.create(
                attempt=attempt, question=question,
                selected_option=options[0] if single else None,
            )
            if not single:
                AttemptResponseSelection.objects.bulk_create([
                    AttemptResponseSelection(response=response, option=option, position=index)
                    for index, option in enumerate(options, 1)
                ])
        if _complete_if_satisfied(assignment, now=now, passed_quiz=passed):
            assignment.save(update_fields=["completed_at"])
            assignment_ended(assignment, now=now)
        return attempt
