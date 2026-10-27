import uuid

from .models import Question
from .quizzes import QuizSubmissionError
from .services import DraftInputError, Problem


def read_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise DraftInputError("A quiz identifier is invalid. Reload the draft and try again.") from None


def _filled_options_problem(index, options):
    if len(options) < 2 or any(not option.text.strip() for option in options):
        return Problem(f"Question {index}: add at least two filled options.", ("question", index))
    return None


class SingleChoiceType:
    single_selection = True
    fixed_options = False
    add_action = "add_single"
    blank_option_texts = ["", ""]
    editor_legend = "Options — select the correct answer"
    editor_template = "training/question_types/_single_choice_editor.html"
    take_template = "training/question_types/_single_choice_take.html"
    ranked = False

    def answer_field(self, question_id):
        return f"answer_{question_id}"

    def read_correct(self, post, question_id, options):
        correct_raw = post.get(f"correct_{question_id}", "")
        return {read_uuid(correct_raw)} if correct_raw else set()

    def content_problems(self, index, options):
        problems = []
        shape_problem = _filled_options_problem(index, options)
        if shape_problem:
            problems.append(shape_problem)
        if sum(option.is_correct for option in options) != 1:
            problems.append(Problem(f"Question {index}: select exactly one correct answer.", ("question", index)))
        return problems

    def answer_problem(self, values, valid_option_ids):
        if len(values) != 1 or not values[0]:
            return "Choose one answer."
        if values[0] not in valid_option_ids:
            return "Choose an answer from this question."
        return None

    def submitted_answer(self, values):
        return values[0]

    def grade(self, options, answer):
        by_id = {option.id: option for option in options}
        if not isinstance(answer, uuid.UUID) or answer not in by_id:
            raise QuizSubmissionError("Choose one valid answer for each single-choice question.")
        return [answer], by_id[answer].is_correct


class TrueFalseType(SingleChoiceType):
    fixed_options = True
    add_action = "add_true_false"
    blank_option_texts = ["True", "False"]

    def content_problems(self, index, options):
        problems = []
        if len(options) != 2 or [option.text for option in options] != ["True", "False"]:
            problems.append(Problem(f"Question {index}: true or false needs True and False options.",
                                    ("question", index)))
        if sum(option.is_correct for option in options) != 1:
            problems.append(Problem(f"Question {index}: select exactly one correct answer.", ("question", index)))
        return problems


class MultipleSelectionType:
    single_selection = False
    fixed_options = False
    add_action = "add_multiple"
    blank_option_texts = ["", ""]
    editor_legend = "Options — select every correct answer"
    editor_template = "training/question_types/_multiple_selection_editor.html"
    take_template = "training/question_types/_multiple_selection_take.html"
    ranked = False

    def answer_field(self, question_id):
        return f"multi_{question_id}"

    def read_correct(self, post, question_id, options):
        raw_correct = post.getlist(f"correct_set_{question_id}")
        correct_ids = {read_uuid(value) for value in raw_correct}
        if len(correct_ids) != len(raw_correct):
            raise DraftInputError("A correct option appears more than once.")
        return correct_ids

    def content_problems(self, index, options):
        problems = []
        shape_problem = _filled_options_problem(index, options)
        if shape_problem:
            problems.append(shape_problem)
        if not any(option.is_correct for option in options):
            problems.append(Problem(f"Question {index}: select at least one correct option.", ("question", index)))
        return problems

    def answer_problem(self, values, valid_option_ids):
        if not values:
            return "Choose at least one option."
        if any(value not in valid_option_ids for value in values):
            return "Choose options from this question only."
        if len(set(values)) != len(values):
            return "Choose each option only once."
        return None

    def submitted_answer(self, values):
        return values

    def grade(self, options, answer):
        by_id = {option.id: option for option in options}
        if (not isinstance(answer, list) or not answer or len(answer) != len(set(answer))
                or not set(answer) <= set(by_id)):
            raise QuizSubmissionError("Choose distinct valid options for each multiple-selection question.")
        return sorted(answer), set(answer) == {option.id for option in options if option.is_correct}


class OrderingType:
    single_selection = False
    fixed_options = False
    add_action = "add_ordering"
    blank_option_texts = ["", "", ""]
    editor_legend = "Options — enter the required sequence"
    editor_template = "training/question_types/_ordering_editor.html"
    take_template = "training/question_types/_ordering_take.html"
    ranked = True

    def answer_field(self, question_id):
        return f"order_{question_id}"

    def read_correct(self, post, question_id, options):
        rank_values = post.getlist(f"rank_{question_id}")
        if len(rank_values) != len(options):
            raise DraftInputError("Ordering rank fields are incomplete.", target=("question", str(question_id)))
        for option, raw_rank in zip(options, rank_values, strict=True):
            if raw_rank == "":
                continue
            try:
                rank = int(raw_rank)
            except (ValueError, TypeError):
                raise DraftInputError("Ordering ranks must be whole numbers.",
                                      target=("rank", option.get("raw_id", str(option["id"])))) from None
            if str(rank) != raw_rank.strip() or not 1 <= rank <= 20:
                raise DraftInputError("Ordering ranks must be whole numbers from 1 to 20.",
                                      target=("rank", option.get("raw_id", str(option["id"]))))
            option["required_rank"] = rank
        seen = set()
        for option in options:
            rank = option["required_rank"]
            if rank is not None and rank in seen:
                raise DraftInputError("Each ordering rank must be used only once.",
                                      target=("rank", option.get("raw_id", str(option["id"]))))
            seen.add(rank)
        return set()

    def content_problems(self, index, options):
        problems = []
        shape_problem = _filled_options_problem(index, options)
        if shape_problem:
            problems.append(shape_problem)
        if {option.required_rank for option in options} != set(range(1, len(options) + 1)):
            problems.append(Problem(f"Question {index}: assign every option one required rank from 1 to {len(options)}.",
                                    ("question", index)))
        if any(option.is_correct for option in options):
            problems.append(Problem(f"Question {index}: ordering uses required ranks, not correct-option flags.",
                                    ("question", index)))
        return problems

    def answer_problem(self, values, valid_option_ids):
        if len(values) != len(valid_option_ids) or any(not value for value in values):
            return "Choose an option for every position."
        if any(value not in valid_option_ids for value in values):
            return "Choose options from this question only."
        if len(set(values)) != len(values):
            return "Choose each option only once."
        return None

    def submitted_answer(self, values):
        return values

    def grade(self, options, answer):
        by_id = {option.id: option for option in options}
        if (not isinstance(answer, list) or len(answer) != len(options)
                or len(answer) != len(set(answer)) or set(answer) != set(by_id)):
            raise QuizSubmissionError("Order every option exactly once.")
        required = sorted(options, key=lambda option: option.required_rank or 0)
        if {option.required_rank for option in options} != set(range(1, len(options) + 1)):
            raise QuizSubmissionError("This ordering question has invalid grading rules.")
        return answer, answer == [option.id for option in required]


QUESTION_TYPES = {
    Question.Type.SINGLE_CHOICE: SingleChoiceType(),
    Question.Type.TRUE_FALSE: TrueFalseType(),
    Question.Type.MULTIPLE_SELECTION: MultipleSelectionType(),
    Question.Type.ORDERING: OrderingType(),
}


def for_type(kind):
    return QUESTION_TYPES[kind]


def for_display(kind):
    """Preserve the single-choice fallback when showing an unknown submitted kind."""
    return QUESTION_TYPES.get(kind, QUESTION_TYPES[Question.Type.SINGLE_CHOICE])


def type_for_add_action(action):
    return next((kind for kind, question_type in QUESTION_TYPES.items()
                 if question_type.add_action == action), None)
