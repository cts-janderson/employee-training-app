# Slice 27: question type presentation

Part of [Deepen the training modules: round 3](deepen-modules-round-3.md). It builds on [Slice 5](05-question-types.md) and [Slice 11](11-quiz-editing.md).

## Problem

Slice 5 gave each question type a class in [question_types.py](../../training/question_types.py), and Slice 11 added `fixed_options` and `add_action`. Validation, grading and authoring now dispatch through `for_type(kind)`. The two quiz templates still branch on type names.

[quiz_editor.html](../../templates/training/quiz_editor.html) has four type branches:

- the options legend, line 65:

  ```django
  <legend>{% if question.type == "ordering" %}Options — enter the required sequence{% elif question.type == "multiple_selection" %}Options — select every correct answer{% else %}Options — select the correct answer{% endif %}</legend>
  ```

- the correct-answer control, lines 69–77: a rank number input for ordering, a checkbox for multiple selection, else a radio;
- the fixed option text, line 78: `{% if question.type == "true_false" %}`, which repeats `TrueFalseType.fixed_options`;
- the Add option button, line 92: `{% if question.type != "true_false" %}`, the same rule again.

The conflict summary also branches, at line 20: `{% if question.type == "ordering" %} · rank ...{% elif option.correct %} · selected as correct{% endif %}`.

The option rows carry inline layout styles on six lines (67, 70, 71, 74, 76 and 82):

```django
<div class="actions" style="margin:.5rem 0">
<label style="margin:0" for="rank-{{ option.id }}">Required rank for option {{ forloop.counter }}</label>
<input style="width:5rem" id="rank-{{ option.id }}" type="number" ...>
<label style="margin:0"><input style="width:auto" type="checkbox" ...> Option {{ forloop.counter }} is correct</label>
<input style="flex:1" id="option-{{ option.id }}" name="option_text_{{ question.id }}" ...>
```

[take_quiz.html](../../templates/training/take_quiz.html) has its own branches:

- line 19: `{% if question.type == "ordering" %}data-ordering{% endif %}` on the question fieldset;
- lines 23–48: position selects for ordering, checkboxes for multiple selection, else radios.

Both quiz editors render `quiz_editor.html`: `quiz_editor` and `_quiz_response` in [quiz_views.py](../../training/quiz_views.py) (lines 34 and 78), and `_render_library_editor` in [library_quiz_views.py](../../training/library_quiz_views.py) (line 41). Adding a question type therefore still means editing both templates by hand.

The `else` branches matter. An unknown type can reach the editor template. `read_question_rows` raises `DraftInputError("A question type or prompt is invalid.")` for a kind not in `QUESTION_TYPES` ([quiz_authoring.py](../../training/quiz_authoring.py) line 47). The view then re-renders the submitted rows with status 400, and `submitted_question_rows` ([authoring_ui.py](../../training/authoring_ui.py) line 105) passes the kind through, or `""` when it is missing. Today such a row renders as single choice.

## Change

### 1. Presentation members on each type

In [question_types.py](../../training/question_types.py), add four class attributes to each type:

| Type | `editor_legend` | `editor_template` | `take_template` | `ranked` |
|---|---|---|---|---|
| `SingleChoiceType` | "Options — select the correct answer" | `_single_choice_editor.html` | `_single_choice_take.html` | `False` |
| `TrueFalseType` | inherited | inherited | inherited | inherited |
| `MultipleSelectionType` | "Options — select every correct answer" | `_multiple_selection_editor.html` | `_multiple_selection_take.html` | `False` |
| `OrderingType` | "Options — enter the required sequence" | `_ordering_editor.html` | `_ordering_take.html` | `True` |

- Template names are full paths under `training/question_types/`, for example `"training/question_types/_ordering_editor.html"`. The underscore follows `_browse_controls.html` and `_quiz_outcomes_chart.html`.
- True/false shares the single-choice radio partials. Its only editor difference is `fixed_options`, which already exists.
- Copy the legend strings from line 65 character for character, including the em dashes.

Add one lookup:

```python
def for_display(kind):
    """Like for_type, but an unknown kind falls back to single choice, as the templates' else branches do."""
```

Use it only where a submitted row is shown again. Keep `for_type` everywhere else, so grading and validation still raise `KeyError` for an unknown type.

### 2. Rows carry the presentation

- `saved_question_rows` and `submitted_question_rows` in [authoring_ui.py](../../training/authoring_ui.py) add `"editor_legend"`, `"editor_template"`, `"fixed_options"` and `"ranked"` from `for_display(kind)`.
- `_question_rows` in [quiz_learner_views.py](../../training/quiz_learner_views.py) adds `"take_template"` and `"ranked"` from the `for_type(question.type)` it already calls.
- Keep every existing key. Leave `rank_field_count_mismatch` (authoring_ui.py line 118) as it is; Slice 5 put `submitted_question_rows` out of scope.

### 3. Editor partials

Create three files in `templates/training/question_types/`. Each renders **the correct-answer control for one option** and nothing else. Move the markup from lines 70–76 unchanged, apart from styles (step 5):

- `_single_choice_editor.html`: the radio `correct_{{ question.id }}` inside its label, "Option N is correct".
- `_multiple_selection_editor.html`: the checkbox `correct_set_{{ question.id }}` inside its label, "Option N is correct".
- `_ordering_editor.html`: the label "Required rank for option N" and the input `rank-{{ option.id }}` named `rank_{{ question.id }}`, with its `aria-invalid` and `aria-describedby`.

`{% include %}` passes the whole context, so `question`, `option` and the option loop's `forloop.counter` work inside the partial.

In `quiz_editor.html`:

- line 65 becomes `<legend>{{ question.editor_legend }}</legend>`;
- lines 69–77 become `{% include question.editor_template %}`;
- line 78 becomes `{% if question.fixed_options %}`;
- line 92 becomes `{% if not question.fixed_options %}`;
- line 20 tests `question.ranked` instead of `question.type == "ordering"`.

The option loop, the hidden `option_id_<qid>` input, the option text, the Remove option button and the error span stay in `quiz_editor.html`. They are the same for every type.

### 4. Learner partials

Create three files in `templates/training/question_types/`. Move each branch of take_quiz.html lines 23–48 unchanged:

- `_single_choice_take.html`: the radio choices, lines 43–47;
- `_multiple_selection_take.html`: "Select every answer that applies." and the checkboxes, lines 36–41;
- `_ordering_take.html`: "Choose each option once, in the order it should appear." and the position selects, lines 24–34.

In `take_quiz.html`:

- lines 23–48 become `{% include question.take_template %}`;
- line 19 tests `question.ranked` instead of `question.type == "ordering"`.

The fieldset, legend and error paragraph stay in `take_quiz.html`. Keep the ordering script at the end of the page. It must run once, after every question is in the page.

### 5. Option row styles move to app.css

Add these rules at the end of [app.css](../../training/static/training/app.css):

```css
.option-row { margin: .5rem 0; }
.option-row .option-label { margin: 0; }
.option-row .option-check { width: auto; }
.option-row .option-rank { width: 5rem; }
.option-row .option-text { flex: 1; }
```

Then replace each inline style with its class:

| Today | Becomes |
|---|---|
| `<div class="actions" style="margin:.5rem 0">` | `<div class="actions option-row">` |
| `<label style="margin:0" ...>` (rank, checkbox and radio labels) | `class="option-label"` |
| `<input style="width:auto" ...>` (checkbox and radio) | `class="option-check"` |
| `<input style="width:5rem" ...>` (rank) | `class="option-rank"` |
| `<input style="flex:1" ...>` (option text) | `class="option-text"` |

- The option text label, `Option N text (required to publish)`, has no style today. Don't add `option-label` to it, or it loses its `.35rem` bottom margin.
- Each selector needs two classes to beat `input[type="checkbox"], input[type="radio"] { width: 1.1rem; ... }` (app.css line 98). `.option-row .option-check` does.
- Compare the editor at 1280px wide before and after, with one question of each type. It must look the same. No media query in app.css targets these elements today.

### What must not change

- Form field names: `question_id`, `question_type`, `question_prompt`, `option_id_<qid>`, `option_text_<qid>`, `correct_<qid>`, `correct_set_<qid>`, `rank_<qid>`, and the learner's `answer_<qid>`, `multi_<qid>` and `order_<qid>`.
- Element ids: `question-N`, `question-heading-<qid>`, `prompt-<qid>`, `rank-<oid>`, `option-<oid>`, `option-error-<oid>`, `order-<qid>-<n>`, `error-<qid>`, `quiz-questions`. Error links such as `href="#rank-<oid>"` point at them.
- Every visible label and legend, character for character.
- The `data-ordering` attribute on ordering fieldsets only.
- Action values: `add_option:`, `review_remove_option:`, the four `add_*` actions.
- An unknown or missing submitted type still renders as single choice.

## Out of scope

- The answer review in [quiz_result.html](../../templates/training/quiz_result.html) (lines 20–22) and [assignment_admin_detail.html](../../templates/training/assignment_admin_detail.html) (lines 78–79). They branch on type to show submitted answers, and their rows come from `submitted_answer_rows`. They can take a `review_template` the same way later.
- `rank_field_count_mismatch` and the rest of `submitted_question_rows`.
- Any change to layout, labels or interaction. See [Follow-on](#follow-on).
- Other inline styles in other templates.

## Tests

Write the pinning tests first, against today's templates, and check that they pass before you change anything. Put them in a new `training/test_question_presentation.py`, reusing the fixture style of `QuizWorkflowTests` in [test_quizzes.py](../../training/test_quizzes.py).

- **Attached-quiz editor**, a quiz with one question of each type, GET:
  - each legend text appears once;
  - single choice renders a radio named `correct_<qid>`; true/false the same; multiple selection a checkbox named `correct_set_<qid>`; ordering an input `id="rank-<oid>"` named `rank_<qid>`;
  - "Required rank for option 1", "Option 1 is correct" and "Option 1 text (required to publish)" appear;
  - true/false shows its fixed text in a hidden `option_text_<qid>` input and has no `add_option:<qid>` or `review_remove_option:` button for its options; the other three types have both.
- **Library quiz editor**: the same quiz contents, GET, same assertions. Both views render the template, so both are pinned.
- **Unknown type**: POST the editor with `question_type` set to `"bogus"`. The 400 response renders that question with a radio `correct_<qid>` and "Options — select the correct answer".
- **Conflict summary**: a stale POST gets 409. The saved ordering question shows "· rank", and a correct single-choice option shows "· selected as correct".
- **Take quiz**: GET for an assignment with one question of each type:
  - fields `answer_<qid>`, `multi_<qid>` and `order_<qid>`;
  - "Select every answer that applies." and "Choose each option once, in the order it should appear.";
  - "Position 1 for question 4", for the ordering question at position 4;
  - `data-ordering` appears exactly once.
  - Submitting an invalid answer still re-renders with `aria-invalid="true"` on that question's controls.
- **After the change only**: the editor response contains no `style=`.
- In [test_question_types.py](../../training/test_question_types.py):
  - `for_display("bogus")` and `for_display("")` return the single-choice type;
  - each type's `editor_template` and `take_template` load with `django.template.loader.get_template`;
  - `ranked` is true only for ordering.

Keep the existing quiz tests unchanged. `test_quizzes.py` line 334 asserts `href="#rank-<oid>"`, and line 437 asserts "Select every answer that applies".

## Done when

- The test command passes.
- `grep -n 'question.type ==\|question.type !=' templates/training/quiz_editor.html templates/training/take_quiz.html` finds no matches.
- `grep -rn 'style=' templates/training/quiz_editor.html templates/training/question_types/` finds no matches.
- `grep -rn "Options — " templates/training/` finds no matches. The legends live in `question_types.py`.
- `.venv/bin/python manage.py makemigrations --check --dry-run` prints "No changes detected".

## Follow-on

Not part of this slice. With each type's editor markup in its own partial and the row styles in classes, these quiz editor improvements become small. Collect them in a later feature slice:

1. **Structural actions lose your place.** Add, move and remove redirect to the page top: `redirect("quiz_editor", training_id=training_id)` in quiz_views.py line 64, and the same in library_quiz_views.py line 94. Return to the changed question, and focus the new field.
2. **Crowded option rows.** Each row repeats "Option N text (required to publish)" and a solid red Remove option button. The text box is squeezed, and ordering option text is truncated even at 1280px.
3. **Narrow screens.** At 390px wide the option rows overflow the card.
4. **Ordering uses typed rank numbers.** Duplicates are caught only on save. A live warning is cheap. Authoring in order instead needs a grading and storage decision.
5. **True/false reads awkwardly.** The radio label and fixed text read "Option 1 is correct True".
6. **A question's type can't change.** Single choice ↔ multiple selection could keep the options.
7. **Add-question buttons.** All four are at the bottom of the page only.
8. **No per-question "unfinished" marker** until the author tries to publish.
9. **The passing threshold** is shown without saying what it means.
10. **No "Preview as learner" link,** although the `preview` route exists. The editor links to it only after a published conflict (quiz_editor.html line 113).

## Follow-on implementation

The editor follow-on was implemented after the visual pass in [quiz-pages-layout.md](quiz-pages-layout.md):

- Structural saves return to the affected question or new field and focus it. Remove actions return to the nearest remaining question or the quiz actions.
- Option rows, narrow layouts, add-question controls near the heading, and the passing-threshold explanation came from the visual pass. The true/false editor now reads each fixed answer as a complete label.
- Ordering questions warn about duplicate ranks while typing; the server still validates on save.
- A single-choice question can become multiple selection and back. Option IDs, text, and order stay the same. Switching to single choice keeps the first selected correct option in display order. True/false and ordering types cannot be converted.
- Saved questions show **Ready** or **Needs work** before publication review. A learner-style preview is available for attached and library quizzes. It uses the saved questions, hides answer keys, and records no responses.

The preview is intentionally separate from the existing administrator version preview, which displays answer rules for publication review. The local workflow remains synthetic; no release gate changed.
