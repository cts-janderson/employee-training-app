# Slice 5: question types

Part of [Deepen the training modules](deepen-modules.md).

## Problem

Adding a question type means editing six places, and ordering rules appear in all of them:

- `read_question_rows`, which reads the correct answers, in [quiz_authoring.py](../../training/quiz_authoring.py)
- `_apply_action`, which holds the blank-question templates, in [quiz_authoring.py](../../training/quiz_authoring.py)
- `quiz_content_problems` in [quiz_authoring.py](../../training/quiz_authoring.py)
- the grading branch of `submit_quiz` in [quizzes.py](../../training/quizzes.py)
- `_question_rows`, which picks the form field name, in [quiz_learner_views.py](../../training/quiz_learner_views.py)
- `_validate_answers` in [quiz_learner_views.py](../../training/quiz_learner_views.py)

## Change

Add `training/question_types.py`, one module that owns everything specific to a question type:
- one small class per type;
- a registry `QUESTION_TYPES = {Question.Type.X: XType(), ...}`;
- `for_type(kind)`, which raises `KeyError` for an unknown type.

| Member | Replaces | Notes |
|---|---|---|
| `single_selection` | `single = question.type in {...}` in `submit_quiz` | true for single choice and true/false |
| `answer_field(question_id)` | prefix logic in `_question_rows` | `answer_`, `multi_` or `order_` |
| `blank_option_texts` | text lists in `quiz_authoring._apply_action` | `["True", "False"]`, `["", ""]` or `["", "", ""]` |
| `read_correct(post, question_id, options)` → `correct_ids` | per-type branch in `read_question_rows` | may set `required_rank` on options; raises `DraftInputError` with the current messages |
| `content_problems(index, options)` → `list[str]` | per-type branches of `quiz_content_problems` | |
| `answer_problem(values, valid_option_ids)` → `str` or `None` | per-type branches of `_validate_answers` | returns the learner messages unchanged |
| `submitted_answer(values)` | `answers[...] = values if ... else values[0]` | |
| `grade(options, answer)` → `(selected_ids, correct)` | per-type branches in `submit_quiz` | raises `QuizSubmissionError` with the current messages; no database access |

Callers keep their loops, error collection and saving. They dispatch through `for_type(question.type)`.

### What stays in the callers

- `quiz_content_problems` keeps the checks that apply to every type:
  - the supported-type problem, raised when the type isn't in the registry;
  - the prompt problem;
  - "only ordering options may have required ranks".
- Within each question, problems must come out **in the same order as today**.
- `submit_quiz` still raises "This quiz contains an unsupported question type." for an unknown type.
- Inside `read_correct` and `grade`, keep the existing check order. The first message raised for a bad input must not change.

### Imports

`question_types.py` imports `Question` from `.models`, `DraftInputError` from `.services` and `QuizSubmissionError` from `.quizzes`. To avoid a cycle, `quizzes.py` and `quiz_authoring.py` should import `question_types` inside the functions that use it, or `QuizSubmissionError` should move to `question_types.py` and be re-exported from `quizzes.py`. Choose one, and run the suite to confirm the import works.

## Out of scope

- `authoring_ui.submitted_question_rows` and `quiz_errors` in [authoring_ui.py](../../training/authoring_ui.py). They reparse the submitted form to show it again and match on message text.

## Tests

Add `training/test_question_types.py`. Use unsaved `QuestionOption(id=uuid4(), is_correct=..., required_rank=...)` instances, so `grade` runs without the database.

- `grade`, for each type: correct, incorrect and invalid answers. Invalid answers raise the same messages `submit_quiz` raises today. An ordering question with invalid ranks raises "This ordering question has invalid grading rules."
- `answer_problem`, for each type: every message.
- `content_problems`, for each type: a valid case and every problem message.

Keep the existing workflow tests in [test_quizzes.py](../../training/test_quizzes.py).

## Done when

- The test command passes.
- `grep -rn "Question.Type.ORDERING" training/quizzes.py training/quiz_learner_views.py` finds no matches.
