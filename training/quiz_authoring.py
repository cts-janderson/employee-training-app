import uuid

from django.db import transaction

from .models import Question, QuestionOption, Quiz
from .services import DraftConflict, DraftInputError, Problem, _locked_draft


def _locked_library_quiz(quiz_id, expected_revision, *, stale_message):
    quiz = Quiz.objects.select_for_update().get(pk=quiz_id, version__isnull=True)
    if quiz.revision != expected_revision:
        raise DraftConflict(stale_message)
    return quiz


def removal_review(quiz, action, *, allow_quiz_removal=True):
    if quiz is None:
        return None
    if action == "review_remove_quiz":
        return {"label": "the entire quiz", "action": "remove_quiz"} if allow_quiz_removal else None
    operation, separator, target = action.partition(":")
    if not separator:
        return None
    if operation == "review_remove_question":
        question = next((item for item in quiz.questions.all() if str(item.id) == target), None)
        return {"label": f"question {question.position}", "action": f"remove_question:{target}"} if question else None
    if operation == "review_remove_option":
        for question in quiz.questions.all():
            option = next((item for item in question.options.all() if str(item.id) == target), None)
            if option:
                return {"label": f"option {option.position} in question {question.position}",
                        "action": f"remove_option:{target}"}
    return None


def read_question_rows(post):
    from .question_types import QUESTION_TYPES, for_type, read_uuid

    ids = post.getlist("question_id")
    types = post.getlist("question_type")
    prompts = post.getlist("question_prompt")
    if len(ids) != len(types) or len(ids) != len(prompts) or len(ids) > 100:
        raise DraftInputError("Question fields are incomplete or too numerous.")
    rows = []
    for raw_id, kind, prompt in zip(ids, types, prompts, strict=True):
        question_id = read_uuid(raw_id)
        if kind not in QUESTION_TYPES or len(prompt) > 2000:
            raise DraftInputError("A question type or prompt is invalid.", target=("question", raw_id))
        option_ids = post.getlist(f"option_id_{question_id}")
        option_texts = post.getlist(f"option_text_{question_id}")
        if len(option_ids) != len(option_texts) or len(option_ids) > 20:
            raise DraftInputError("Option fields are incomplete or too numerous.", target=("question", raw_id))
        options = []
        for raw_option_id, option_text in zip(option_ids, option_texts, strict=True):
            if len(option_text) > 500:
                raise DraftInputError("An option is too long.", target=("option", raw_option_id))
            options.append({"id": read_uuid(raw_option_id), "raw_id": raw_option_id,
                            "text": option_text, "required_rank": None})
        option_set = {option["id"] for option in options}
        if len(option_set) != len(options):
            raise DraftInputError("An option appears more than once.")

        try:
            correct_ids = for_type(kind).read_correct(post, question_id, options)
        except DraftInputError as exc:
            if exc.target and exc.target[0] == "question":
                exc.target = ("question", raw_id)
            raise
        if not correct_ids <= option_set:
            raise DraftInputError("A correct answer is not an option in its question.",
                                  target=("question", raw_id))
        rows.append({"id": question_id, "type": kind, "prompt": prompt,
                     "options": options, "correct_ids": correct_ids})
    if len({row["id"] for row in rows}) != len(rows):
        raise DraftInputError("A question appears more than once.")
    return rows


def _apply_action(rows, action):
    from .question_types import for_type, read_uuid, type_for_add_action

    if action == "save":
        return rows
    kind = type_for_add_action(action)
    if kind is not None:
        if len(rows) >= 100:
            raise DraftInputError("A quiz may contain at most 100 questions.")
        texts = for_type(kind).blank_option_texts
        return [*rows, {
            "id": uuid.uuid4(), "type": kind, "prompt": "", "correct_ids": set(),
            "options": [{"id": uuid.uuid4(), "text": value, "required_rank": None} for value in texts],
        }]
    operation, separator, raw_id = action.partition(":")
    if not separator or operation not in {"remove_question", "up_question", "down_question", "add_option", "remove_option", "change_type"}:
        raise DraftInputError("Unknown quiz action.")
    if operation == "change_type":
        raw_id, separator, new_type = raw_id.partition(":")
        if not separator or new_type not in {Question.Type.SINGLE_CHOICE, Question.Type.MULTIPLE_SELECTION}:
            raise DraftInputError("Choose a supported question type.")
    target = read_uuid(raw_id)
    if operation == "change_type":
        row = next((row for row in rows if row["id"] == target), None)
        if row is None or row["type"] not in {Question.Type.SINGLE_CHOICE, Question.Type.MULTIPLE_SELECTION}:
            raise DraftInputError("Only single-choice and multiple-selection questions can change type.")
        if row["type"] == new_type:
            return rows
        if new_type == Question.Type.SINGLE_CHOICE and row["correct_ids"]:
            row["correct_ids"] = {next(option["id"] for option in row["options"]
                                       if option["id"] in row["correct_ids"])}
        row["type"] = new_type
        return rows
    if operation == "remove_question":
        if target not in {row["id"] for row in rows}:
            raise DraftInputError("The selected question was not found.")
        return [row for row in rows if row["id"] != target]
    if operation in {"up_question", "down_question"}:
        index = next((index for index, row in enumerate(rows) if row["id"] == target), None)
        if index is None:
            raise DraftInputError("The selected question was not found.")
        other = index - 1 if operation == "up_question" else index + 1
        if 0 <= other < len(rows):
            rows[index], rows[other] = rows[other], rows[index]
        return rows
    if operation == "add_option":
        row = next((row for row in rows if row["id"] == target), None)
        if row is None or for_type(row["type"]).fixed_options or len(row["options"]) >= 20:
            raise DraftInputError("Cannot add an option to this question.")
        row["options"].append({"id": uuid.uuid4(), "text": "", "required_rank": None})
        return rows
    row = next((row for row in rows if any(option["id"] == target for option in row["options"])), None)
    if row is None or for_type(row["type"]).fixed_options:
        raise DraftInputError("The selected option was not found.")
    row["options"] = [option for option in row["options"] if option["id"] != target]
    row["correct_ids"].discard(target)
    return rows


def quiz_publication_problems(version):
    quiz = Quiz.objects.filter(version=version).first()
    return quiz_content_problems(quiz)


def quiz_content_problems(quiz):
    from .question_types import QUESTION_TYPES, for_type

    if quiz is None:
        return []
    problems = []
    if quiz.passing_percent is None or not 1 <= quiz.passing_percent <= 100:
        problems.append(Problem("Quiz: set an integer passing threshold from 1 to 100.", ("quiz", "threshold")))
    questions = list(quiz.questions.prefetch_related("options"))
    if not questions:
        problems.append(Problem("Quiz: add at least one question.", ("quiz", None)))
    for index, question in enumerate(questions, 1):
        options = list(question.options.all())
        if question.type not in QUESTION_TYPES:
            problems.append(Problem(f"Question {index}: choose a supported type.", ("question", index)))
        if not question.prompt.strip():
            problems.append(Problem(f"Question {index}: add a prompt.", ("question", index)))
        if question.type in QUESTION_TYPES:
            problems.extend(for_type(question.type).content_problems(index, options))
        if question.type != Question.Type.ORDERING and any(option.required_rank is not None for option in options):
            problems.append(Problem(f"Question {index}: only ordering options may have required ranks.",
                                    ("question", index)))
    return problems


def library_quiz_problems(quiz):
    problems = []
    if not quiz.title.strip():
        problems.append(Problem("Quiz: add a title before attaching.", ("title",)))
    problems.extend(quiz_content_problems(quiz))
    return problems


def save_quiz_draft(*, version_id, expected_revision, threshold_value, rows, action):
    with transaction.atomic():
        version = _locked_draft(
            version_id, expected_revision,
            stale_message="A newer draft was saved. Reload before editing again.",
        )
        quiz = Quiz.objects.filter(version=version).first()
        if action == "add_quiz":
            if quiz is not None:
                raise DraftInputError("This draft already has a quiz.")
            Quiz.objects.create(version=version, is_demo=version.training.is_demo)
        elif action == "remove_quiz":
            if quiz is None:
                raise DraftInputError("This draft has no quiz.")
            QuestionOption.objects.filter(question__quiz=quiz).delete()
            quiz.questions.all().delete()
            quiz.delete()
        else:
            if quiz is None:
                raise DraftInputError("Add a quiz first.")
            _save_quiz_contents(quiz, threshold_value, rows, action)
        version.revision += 1
        version.save(update_fields=["revision"])
        return version


def save_library_quiz(*, quiz_id, expected_revision, title, threshold_value, rows, action):
    if len(title) > 200:
        raise DraftInputError("The quiz title is too long.", target=("title",))
    if action in {"add_quiz", "remove_quiz"}:
        raise DraftInputError("This library quiz already exists.")
    with transaction.atomic():
        quiz = _locked_library_quiz(
            quiz_id, expected_revision,
            stale_message="A newer library quiz was saved. Reload before editing again.",
        )
        _save_quiz_contents(quiz, threshold_value, rows, action)
        quiz.title = title
        quiz.revision += 1
        quiz.save(update_fields=["title", "revision"])
        return quiz


def _save_quiz_contents(quiz, threshold_value, rows, action):
    from .question_types import for_type

    existing = {question.id: question for question in quiz.questions.prefetch_related("options")}
    if set(existing) != {row["id"] for row in rows}:
        raise DraftInputError("The question list changed. Reload the quiz and try again.")
    for row in rows:
        question = existing[row["id"]]
        if (row["type"] != question.type
                or {option["id"] for option in row["options"]} != set(question.options.values_list("id", flat=True))):
            raise DraftInputError("The quiz structure changed. Reload the quiz and try again.")
        question_type = for_type(row["type"])
        if question_type.fixed_options and [option["text"] for option in row["options"]] != question_type.blank_option_texts:
            raise DraftInputError("True or false options must remain True and False.")
    if threshold_value == "":
        threshold = None
    else:
        try:
            threshold = int(threshold_value)
        except (ValueError, TypeError):
            raise DraftInputError("Enter an integer passing threshold.", target=("quiz", "threshold")) from None
        if str(threshold) != str(threshold_value).strip() or not 1 <= threshold <= 100:
            raise DraftInputError("Enter an integer passing threshold from 1 to 100.",
                                  target=("quiz", "threshold"))
    rows = _apply_action(rows, action)
    quiz.passing_percent = threshold
    quiz.save(update_fields=["passing_percent"])
    QuestionOption.objects.filter(question__quiz=quiz).delete()
    quiz.questions.all().delete()
    Question.objects.bulk_create([
        Question(id=row["id"], quiz=quiz, position=index, type=row["type"], prompt=row["prompt"])
        for index, row in enumerate(rows, 1)
    ])
    QuestionOption.objects.bulk_create([
        QuestionOption(id=option["id"], question_id=row["id"], position=index,
                       text=option["text"], is_correct=option["id"] in row["correct_ids"],
                       required_rank=option["required_rank"])
        for row in rows for index, option in enumerate(row["options"], 1)
    ])
