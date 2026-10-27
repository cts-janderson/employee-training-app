# Slice 10: structured draft problems

Part of [Deepen the training modules: round 2](deepen-modules-round-2.md).

## Problem

The authoring pages work out which form field an error belongs to by matching words in its message, and by checking the domain's limits a second time:

| Matcher | Re-derives |
|---|---|
| `draft_errors` in [authoring_ui.py](../../training/authoring_ui.py) | The title and description limits 200 and 5000 (`save_draft`), the slide field limits 200, 10000 and 500 (`save_draft`), and words such as "training title", "Slide ", "Quiz:", "Question " and "threshold" |
| `quiz_errors` in [authoring_ui.py](../../training/authoring_ui.py) | The ordering rank rule from `OrderingType.read_correct` (`isdigit`, length 2 or less, 1–20, used only once), the option limit 500 and the prompt limit 2000 from `read_question_rows`, and words such as "Option fields", "correct answer" and "passing threshold" |
| `_render_library_editor` in [library_quiz_views.py](../../training/library_quiz_views.py) | `"title" in message.lower()` |
| `_profile_field_error` in [employee_views.py](../../training/employee_views.py) | `"deactivat"`, `"name"` |

The domain module always knows which field failed, but it hands over a plain string. Rewording a message, or changing a limit in one place, breaks the error placement without any error.

## Change

### 1. A problem that is still a string

Add this to [services.py](../../training/services.py):

```python
class Problem(str):
    """A user-facing message that also names the form field it belongs to."""

    def __new__(cls, message, target=None):
        problem = super().__new__(cls, message)
        problem.target = target
        return problem
```

A `Problem` compares equal to its message. Existing tests that compare lists of strings therefore keep passing, for example `content_problems` in [test_question_types.py](../../training/test_question_types.py).

Give `DraftInputError` and `EmployeeInputError` an optional `target` keyword, stored as `self.target`. The message argument stays first and unchanged.

Targets are small tuples. They describe the domain, not HTML:

| Target | Meaning |
|---|---|
| `("title",)` / `("description",)` | The training or library quiz title, or the training description |
| `("slide", position, field)` | A 1-based slide position, with `field` in `"header"`, `"body"`, `"footer"` or `None` |
| `("quiz", field)` | `field` in `"threshold"` or `None` |
| `("question", position_or_id)` | A question's prompt or structure |
| `("option", option_id)` / `("rank", option_id)` | An option's text, or its ordering rank |
| `("profile", field)` | `field` in `"name"`, `"email"` or `"active"` |

`("question", ...)` takes a position for publication problems and an id for save errors.

IDs in targets are the **submitted strings**, not parsed UUIDs. The UI rows hold string IDs, and some checks run before the ID is parsed. For example, the option-length check in `read_question_rows` runs before `_uuid(raw_option_id)`, so its target uses `raw_option_id`.

`Problem` keeps `.target` when pickled, but loses it when serialized to JSON. Django's default session serializer is JSON. Never store a `Problem` in the session and expect its target back. Nothing does that today.

### 2. Domain modules set the target

Messages stay character for character. Only the target is new.

- [services.py](../../training/services.py):
  - `save_draft`:
    - For the title/description limit, the target is `("title",)` if the title is too long, and `("description",)` otherwise. This is today's precedence.
    - For a slide field, the target is `("slide", position, field)` for the first field over its limit, in header, body, footer order.
  - `_publication_problems` returns `Problem`s: `("title",)` and `("slide", index, "header" | "body")`.
- [quiz_authoring.py](../../training/quiz_authoring.py):
  - `read_question_rows` sets `("question", question_id)` on the type/prompt and option-field errors, and on "A correct answer is not an option…". It sets `("option", option_id)` on "An option is too long."
  - `quiz_content_problems` returns `Problem`s, with `("quiz", "threshold")`, `("quiz", None)` and `("question", index)`.
  - `_save_quiz_contents` sets `("quiz", "threshold")` on both threshold errors.
  - `save_library_quiz` sets `("title",)` on "The quiz title is too long."
  - `library_quiz_problems` returns its title problem with `("title",)`.
- [question_types.py](../../training/question_types.py):
  - `OrderingType.read_correct` sets `("rank", option["id"])` on the whole-number errors, `("rank", <second option using the rank>)` on "Each ordering rank must be used only once.", and `("question", question_id)` on "Ordering rank fields are incomplete."
  - Each `content_problems` returns `Problem`s targeted `("question", index)`.
- [employees.py](../../training/employees.py): `_validated_profile` sets `("profile", "name")` or `("profile", "email")`. The deactivation error sets `("profile", "active")`.

### 3. The UI places targets

- **`draft_errors` and `quiz_errors`:** they take problems, which may be `Problem`s or plain strings, and map each target to today's anchors:
  - title: `#title`
  - description: `#description`
  - slide field: `#{field}-{slide_id}` (no field: `#slide-{slide_id}`)
  - threshold: `#passing-percent`
  - other quiz problem: `#quiz-actions`
  - question in the training editor: the quiz editor URL plus `#question-{n}`
  - question in the quiz editor: `#prompt-{id}`
  - option: `#option-{id}`
  - rank: `#rank-{id}`
- **Every place that turns an exception into a string** must keep the target. Pass `Problem(str(exc), getattr(exc, "target", None))` instead:
  - `draft_editor` in [views.py](../../training/views.py);
  - `quiz_editor` in [quiz_views.py](../../training/quiz_views.py);
  - `library_quiz_editor` in [library_quiz_views.py](../../training/library_quiz_views.py), which builds `messages=[str(exc)]` for `_render_library_editor`;
  - `employee_directory` and `edit_employee` in [employee_views.py](../../training/employee_views.py), which set `error = str(exc)`.
- **`_render_library_editor`:** use the `("title",)` target instead of `"title" in message.lower()`.
- **`_profile_field_error`:** takes the `Problem` and reads `problem.target[1]`. The `IntegrityError` branch builds `Problem("An employee already uses that email.", ("profile", "email"))`. The template still receives the same `error` string and `error_field`.
- A problem with no target falls back to today's default anchor: `#draft-actions`, `#quiz-actions` or the first question. Delete every limit, `isdigit` and substring check from [authoring_ui.py](../../training/authoring_ui.py).

Some anchors become more precise. For example, an option-length error now points at the exact option, not at the first long option in the first question that has one. No existing test may change.

## Out of scope

- The learner quiz form's errors in `_validate_answers`. They already carry an anchor.
- Changing any message text.
- `PublicationBlocked.problems` stays a list. It now holds `Problem`s.

## Tests

- Domain tests in [tests.py](../../training/tests.py), [test_quizzes.py](../../training/test_quizzes.py) and [test_question_types.py](../../training/test_question_types.py). Assert `exc.target`, or `problem.target`, for:
  - a long title and a long description;
  - a long slide body on slide 2;
  - a missing slide header (publication);
  - an invalid rank, a duplicate rank and a long option;
  - an invalid threshold;
  - a library quiz with a long title;
  - each employee profile error.
- In [test_question_types.py](../../training/test_question_types.py), assert that `content_problems` returns `Problem`s whose target is `("question", index)`.
- `Problem` equality ignores the target, so the existing string assertions can't verify placement. Add placement tests through the views:
  - the library quiz title error anchors to `#quiz-title`;
  - each employee profile error sets the right `error_field`, including a duplicate email;
  - a publication blocker for slide 2's body links to that slide's body field;
  - in the quiz editor, with a valid first question and a too-long option in the second question, the error anchors to that option.
- Keep every existing `assertContains` on anchors.

Django's own field-error tools, `ValidationError(code=..., params=...)` and forms' `add_error(field, ...)`, would do the same job. Adopting forms is wider than this slice. If the editors later move to Django forms, replace `Problem` with them.

## Done when

- The test command passes.
- `grep -n "\.lower()\|isdigit\|10000\|5000\|2000" training/authoring_ui.py training/employee_views.py training/library_quiz_views.py` finds no matches.
