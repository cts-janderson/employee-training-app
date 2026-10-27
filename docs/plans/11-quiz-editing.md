# Slice 11: quiz editing

Part of [Deepen the training modules: round 2](deepen-modules-round-2.md).

## Problem

An attached quiz and a library quiz differ only in where the quiz lives. Even so, the editing steps are written twice:

| Step | Attached quiz / training draft | Library quiz |
|---|---|---|
| Find the draft, or else the latest version on POST | `draft_editor` ([views.py](../../training/views.py)) and `quiz_editor` ([quiz_views.py](../../training/quiz_views.py)), both copies | n/a |
| Removal review: work out the label and action for "remove question N" or "remove option M in question N" | `quiz_editor` | `library_quiz_editor` ([library_quiz_views.py](../../training/library_quiz_views.py)), almost line for line |
| Lock the quiz and check its revision | `_locked_draft` | `save_library_quiz` and `attach_library_quiz` ([services.py](../../training/services.py)), both copies |

There is also dead code:
- In `draft_editor`, the `expected_revision != version.revision` check inside the `review_remove:` branch can never be true, because the earlier pre-check already returned.
- The same goes for the matching check inside `quiz_editor`'s `review_remove` branch.
- `if request.method == "POST":` in `quiz_editor` is always true at that point.

Slice 5 also left some question-type rules in [quiz_authoring.py](../../training/quiz_authoring.py):
- **True/false fixed options:** checked in `_apply_action`'s `add_option` and `remove_option` branches, and in `_save_quiz_contents` ("True or false options must remain True and False.").
- **The add-action map:** `add_single`, `add_true_false`, `add_multiple` and `add_ordering` → type, inline in `_apply_action`.
- **The type check:** `read_question_rows` checks `kind not in Question.Type.values` instead of the registry.
- **A duplicate helper:** `_uuid` duplicates `question_types._read_uuid`, message included.

## Change

### 1. Removal review in the domain

Add this to [quiz_authoring.py](../../training/quiz_authoring.py):

```python
def removal_review(quiz, action):
    """Return {"label", "action"} for a review_remove_* action against the saved quiz, or None."""
```

It covers `review_remove_quiz`, `review_remove_question:<id>` and `review_remove_option:<id>`. Its labels must match today's text character for character:
- "the entire quiz"
- "question {position}"
- "option {position} in question {position}"

Both quiz editor views call it:
- `quiz_editor` passes its quiz.
- `library_quiz_editor` passes the library quiz, and must return `None` for `review_remove_quiz`. Make that explicit with a `allow_quiz_removal=False` keyword.

Leave `draft_editor`'s slide removal review as it is. It reviews slides, not questions.

### 2. One way to find the editable version

Add `editable_version(training_id, *, for_post)` at the top of [quiz_views.py](../../training/quiz_views.py), and import it from [views.py](../../training/views.py). It raises `Http404`, so it belongs with the views, not in [services.py](../../training/services.py).
- It returns the draft if there is one.
- Otherwise, if `for_post` is true, it returns the latest version.
- It raises `Http404` with today's messages: "Draft not found." or "Training version not found."

Use it in `draft_editor` and `quiz_editor`.

### 3. One library quiz lock

Add `_locked_library_quiz(quiz_id, expected_revision, *, stale_message)` to [quiz_authoring.py](../../training/quiz_authoring.py), following `_locked_draft`:
- It locks with `Quiz.objects.select_for_update().get(pk=quiz_id, version__isnull=True)`.
- It raises `DraftConflict(stale_message)` if the revision doesn't match.

Use it in `save_library_quiz` and `attach_library_quiz`. Keep each caller's current message.

[services.py](../../training/services.py) already imports from `.quiz_authoring` inside `attach_library_quiz`. Import the lock there too.

### 4. Remove the dead code

- Delete the inner revision check in `draft_editor`'s `review_remove:` branch.
- Delete the matching check in `quiz_editor`.
- Remove the always-true `if request.method == "POST":` in `quiz_editor`, and dedent its body.

Keep the outer pre-checks: `version.status != DRAFT or expected_revision != version.revision` in `draft_editor` and `quiz_editor`, and `revision != quiz.revision` in `library_quiz_editor`.
- The `cancel_remove` and `review_remove` paths never reach the domain, so the pre-check is their only conflict guard.
- On the save path, the pre-check sets which error wins. Today a stale or published draft gets the 409 conflict page **before** any input is parsed. Without the pre-check, `read_slide_rows`, `read_question_rows` and `save_draft`'s length checks would run first. A stale submission with bad input would then get a 400 instead of a 409. The locked check in `_locked_draft` stays the authoritative guard against a concurrent save, and the pre-check is a cheap early answer.

### 5. Finish the Slice 5 leftovers

In [question_types.py](../../training/question_types.py):
- Add `fixed_options = False` to each type, and `fixed_options = True` to `TrueFalseType`.
- Add an `add_action` name to each type: `"add_single"`, `"add_true_false"`, `"add_multiple"` or `"add_ordering"`.
- Add `type_for_add_action(action)`. It returns the kind, or `None`.

In [quiz_authoring.py](../../training/quiz_authoring.py):
- `_apply_action` uses `type_for_add_action`, and uses `for_type(row["type"]).fixed_options` in the `add_option` and `remove_option` branches.
- `_save_quiz_contents` checks `fixed_options` together with the type's `blank_option_texts`, raising the same message.
- `read_question_rows` checks `kind not in QUESTION_TYPES`.
- `_uuid` becomes an import of `question_types._read_uuid`. Rename it to `read_uuid` if you import it across modules.

## Out of scope

- Merging `save_quiz_draft` and `save_library_quiz` into one function. Their container steps differ: the version revision versus the quiz title and revision, and add or remove quiz. Two adapters at one seam are worth it only once the views share more than this.
- Changing how library quizzes are copied ([ADR-0003](../adr/0003-copy-library-quiz-into-draft.md)).
- `new_library_quiz` calling `Quiz.objects.create()` in the view. Moving one line into a function fails the deletion test.

## Tests

- `removal_review` in [test_library_quizzes.py](../../training/test_library_quizzes.py) or [test_quizzes.py](../../training/test_quizzes.py):
  - each label;
  - an unknown id returns `None`;
  - `review_remove_quiz` with `allow_quiz_removal=False` returns `None`.
- `_locked_library_quiz` raises with the caller's message when the revision is stale.
- `editable_version` returns the draft, falls back to the latest version only when `for_post` is true, and raises both 404 messages.
- [test_question_types.py](../../training/test_question_types.py):
  - `type_for_add_action` for all four actions and an unknown one;
  - `fixed_options` is true only for true/false.
- View tests for **both** the attached-quiz editor and the library quiz editor:
  - a malformed `review_remove_option:not-a-uuid` and an unknown ID both fall through to today's response;
  - a stale `review_remove_*` POST gets the 409 conflict page;
  - a stale submission with invalid fields still gets 409, not 400. This pins the precedence from step 4.

## Done when

- The test command passes.
- `grep -n "Question.Type.TRUE_FALSE\|Question.Type.values" training/quiz_authoring.py` finds no matches.
- The review-remove label strings appear only in `quiz_authoring.py`.
