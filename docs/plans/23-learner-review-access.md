# Slice 23: learner review access

Part of [Deepen the training modules: round 3](deepen-modules-round-3.md). Do [Slice 22](22-assignment-status.md) first; it fixes the waived deadline on the quiz result page.

## Problem

[Slice 12](12-learner-access.md) gave learners one access decision, `learner_access` in [assignments.py](../../training/assignments.py) (lines 498-514). It returns `(assignment, denial)`. [Slice 19](19-waivers.md) then added `LearnerDenial.WAIVED` (line 53). That isn't really a denial. It means "you may review this assignment, but you may not act on it". Nothing in the domain says so. Each view works it out for itself:

| File | Line | Code |
|---|---|---|
| [assignment_views.py](../../training/assignment_views.py) | 256 (`assignment_detail`) | `if denial and denial != LearnerDenial.WAIVED:` |
| [assignment_views.py](../../training/assignment_views.py) | 282 (`assignment_slide`) | `if denial and denial != LearnerDenial.WAIVED:` |
| [assignment_views.py](../../training/assignment_views.py) | 287 (`assignment_slide`) | `if denial != LearnerDenial.WAIVED:` before `visit_slide(...)` |
| [assignment_views.py](../../training/assignment_views.py) | 309-310 (`acknowledge`) | `if denial == LearnerDenial.WAIVED: return HttpResponseForbidden("This assignment was waived.")` |
| [quiz_learner_views.py](../../training/quiz_learner_views.py) | 85-86 (`take_quiz`) | `return _unavailable_quiz(request, assignment, "This assignment was waived.")` |
| [quiz_learner_views.py](../../training/quiz_learner_views.py) | 110-111 (`submit_quiz_view`) | `return _unavailable_quiz(request, assignment, "This assignment was waived.", status=403)` |
| [quiz_learner_views.py](../../training/quiz_learner_views.py) | 146-147 (`submit_quiz_view`, `except AssignmentAccessError`) | the same again, after a second `learner_access` call |
| [quiz_learner_views.py](../../training/quiz_learner_views.py) | 170 (`quiz_result`) | `if denial and denial != LearnerDenial.WAIVED:` |

The message "This assignment was waived." is written five times: four times in the views, and once in `_owned_assignment` ([assignments.py](../../training/assignments.py) line 526). The other denial messages are copied the same way. "This assignment is not available to you." is at [assignments.py](../../training/assignments.py) line 522 and [quiz_learner_views.py](../../training/quiz_learner_views.py) line 104. "This assignment was canceled." is at [assignments.py](../../training/assignments.py) line 524 and [quiz_learner_views.py](../../training/quiz_learner_views.py) line 84.

Completed work is also read-only for the learner, but through separate `completed_at` checks: [quiz_learner_views.py](../../training/quiz_learner_views.py) lines 88, 124 and 153, [assignment_views.py](../../training/assignment_views.py) line 318, and the domain's `acknowledge_slide`, `visit_slide` and `submit_quiz`.

Today's responses were captured by hand against the current code, with a published version that has one slide and a one-question quiz. Each row has its slide acknowledged and one failed attempt before it ends:

| Learner's assignment | `assignment_detail` | `assignment_slide` | `acknowledge` | `take_quiz` | `submit_quiz_view` | `quiz_result` |
|---|---|---|---|---|---|---|
| Missing | 404 | 404 | 404 | 404 | 404 "Assignment not found." | 404 |
| Another employee's | 404 | 404 | 404 | 404 | 403 "This assignment is not available to you." | 404 |
| Own, outstanding | 200 | 200 | 302 to detail `?slide_acknowledged=1` | 200 | 400 `take_quiz.html` for bad answers; 302 to the result for a new submission | 200 |
| Own, completed | 200 | 200 | 302 to detail `?slide_acknowledged=1` | 302 to detail | 400 `quiz_unavailable.html` "This assignment is already complete. Review its current result and attempt history."; 302 to the result when the passing submission is replayed | 200 |
| Own, canceled | 404 | 404 | 404 | 409 `quiz_unavailable.html` "This assignment was canceled." | 403 `quiz_unavailable.html` "This assignment was canceled before your quiz was submitted." | 404 |
| Own, waived | 200 | 200 | 403 "This assignment was waived." | 409 `quiz_unavailable.html` "This assignment was waived." | 403 `quiz_unavailable.html` "This assignment was waived." | 200 |

`test_learner_views_preserve_foreign_and_canceled_responses` ([test_deep_modules_round_2.py](../../training/test_deep_modules_round_2.py) lines 175-196) already pins the status codes for the foreign and canceled rows. Its fixture has no quiz. `test_learner_can_review_waived_work_but_cannot_acknowledge` ([test_waivers.py](../../training/test_waivers.py) lines 55-64) checks the waived row for the detail page, the slide and `acknowledge` only. It checks status codes and the "Assignment waived" text. Nothing pins the waived quiz views or the completed row.

`test_learner_access_classifies_missing_foreign_inactive_and_canceled` ([test_deep_modules_round_2.py](../../training/test_deep_modules_round_2.py) line 162) asserts `learner_access(assignment.id, self.alice) == (assignment, None)`. Lines 163-173 index the result with `[1]`. So the new decision must still compare equal to, and index like, a two-item tuple.

## Decision needed

Should completed assignments get review access only, like waived ones, in this slice?

They are read-only in practice, but they don't respond like waived work. These cells differ:

| View | Completed today | Waived today |
|---|---|---|
| `acknowledge` | 302 to detail; `acknowledge_slide` returns without a change | 403 "This assignment was waived." |
| `take_quiz` | 302 to detail | 409 `quiz_unavailable.html` |
| `submit_quiz_view`, replay of the passing submission | 302 to the result. `submit_quiz` returns the existing attempt; `test_submission_replay_preserves_attempt_and_completion_and_rejects_new_attempt` ([test_quizzes.py](../../training/test_quizzes.py) line 184) relies on it. | 403 `quiz_unavailable.html` |
| `submit_quiz_view`, new or invalid submission | 400 `quiz_unavailable.html` "This assignment is already complete. …", asserted by [test_quizzes.py](../../training/test_quizzes.py) line 620 | 403 `quiz_unavailable.html` "This assignment was waived." |

The detail page, the slide view and the quiz result are the same for both: 200. In the slide view, skipping `visit_slide` for completed work would also leave the response unchanged, because `visit_slide` saves nothing once `completed_at` is set. But the view would still need a completion check, so that gains nothing.

- **A. Leave completion out (recommended).** `learner_access` gives completed work the act level, as today. Requests still reach the domain, which decides what completed work accepts: an acknowledgment changes nothing, a replay returns the attempt, and a new attempt gets "already complete". No status code or message changes. Because completed work stays at the act level, the act level is not the glossary's **actionable assignment**. Don't name it that.
- **B. Fold completion into review access.** The four cells above change to the waived responses. A learner who double-submits a passing quiz would get 403 instead of the result. This changes the assertion at [test_quizzes.py](../../training/test_quizzes.py) line 620. If `_owned_assignment` also refuses completed work, it breaks [test_quizzes.py](../../training/test_quizzes.py) line 184 and the domain's own replay rule. The act level would then match **actionable assignment** exactly. This would need its own plan, naming each changed test.
- **C. Fold completion in, but keep the completed responses** by checking completion in `acknowledge`, `take_quiz` and `submit_quiz_view`. This moves the view-level interpretation rather than removing it. Reject.

**Decision: Option A**, recorded 2026-10-27. Completed assignments keep the act level and today's responses.

## Change

### 1. Glossary

This slice introduces one concept. Add it to [GLOSSARY.md](../GLOSSARY.md), after **Actionable assignment**:

> **Review access**:
> An employee's access to their own assignment's slides and quiz attempt history without the ability to acknowledge slides or submit quiz attempts. A waived assignment offers its employee review access only.
> _Avoid_: Read-only mode, view access

If option B is chosen later, that plan adds completed assignments to the definition.

### 2. An access decision with a level

In [assignments.py](../../training/assignments.py), next to `LearnerDenial` (lines 48-53):

```python
class LearnerAccessLevel:
    NONE = "none"
    REVIEW = "review"
    ACT = "act"


_DENIAL_MESSAGES = {
    LearnerDenial.NOT_YOURS: "This assignment is not available to you.",
    LearnerDenial.INACTIVE: "This assignment is not available to you.",
    LearnerDenial.CANCELED: "This assignment was canceled.",
    LearnerDenial.WAIVED: "This assignment was waived.",
}


class LearnerAccess(NamedTuple):
    """What a learner may do with an assignment; WAIVED gives review access only."""
    assignment: Assignment | None
    denial: str | None

    @property
    def level(self): ...  # None -> ACT, WAIVED -> REVIEW, any other denial -> NONE
    @property
    def can_review(self): ...  # level != NONE
    @property
    def can_act(self): ...  # level == ACT
    @property
    def message(self): ...  # _DENIAL_MESSAGES.get(self.denial); None for MISSING and for no denial
```

- Use `NamedTuple` from `typing`. It keeps `learner_access(...) == (assignment, None)`, `[1]` and `assignment, denial = ...` working, so the existing tests pass unchanged.
- Keep the names `LearnerDenial` and `denial`. Renaming them widens the slice. The docstring records that `WAIVED` limits the learner to review.
- `learner_access` returns `LearnerAccess(...)` in each branch. Its checks, their order and its `lock` and `queryset` keywords don't change.
- `MISSING` has no message. Each view keeps its own "Assignment not found." text, as Slice 12 decided.

### 3. `_owned_assignment` uses the decision

```python
def _owned_assignment(assignment_id, employee):
    access = learner_access(assignment_id, employee, lock=True)
    if access.denial == LearnerDenial.MISSING:
        raise Assignment.DoesNotExist
    if not access.can_act:
        raise AssignmentAccessError(access.message)
    return access.assignment
```

The raise mapping is the same as today's (lines 517-527): `DoesNotExist` for `MISSING`, and `AssignmentAccessError` with the same message for every other denial.

### 4. Views ask the decision

In [assignment_views.py](../../training/assignment_views.py):

- `assignment_detail`, `assignment_slide` and, in [quiz_learner_views.py](../../training/quiz_learner_views.py), `quiz_result`: `access = learner_access(...)`, then `if not access.can_review: raise Http404("Assignment not found.")`.
- `assignment_slide`: call `visit_slide` only `if access.can_act`.
- `acknowledge`: `if access.level == LearnerAccessLevel.REVIEW: return HttpResponseForbidden(access.message)`, then `if not access.can_act: raise Http404("Assignment not found.")`. The REVIEW check comes first, as the waived check does today.
- Import `LearnerAccessLevel` and drop the now-unused `LearnerDenial` import.

In [quiz_learner_views.py](../../training/quiz_learner_views.py):

- `take_quiz`: keep the 404 for `{MISSING, NOT_YOURS, INACTIVE}`. Then `if not access.can_act: return _unavailable_quiz(request, access.assignment, access.message)`. That covers both canceled ("This assignment was canceled.") and waived, at 409.
- `submit_quiz_view`: keep the `MISSING` 404 and the `NOT_YOURS`/`INACTIVE` 403, but use `access.message` for the 403 text. Move the canceled and review-only responses into one helper, used both at the top and in the `except AssignmentAccessError` branch:

  ```python
  _CANCELED_BEFORE_SUBMISSION = "This assignment was canceled before your quiz was submitted."

  def _refused_submission(request, access):
      """The quiz-unavailable response for a canceled or review-only assignment, or None."""
  ```

  It returns `_unavailable_quiz(..., _CANCELED_BEFORE_SUBMISSION, status=403)` for `CANCELED`, and `_unavailable_quiz(..., access.message, status=403)` for `REVIEW`. In the `except` branch, return its result when it isn't `None`; otherwise return `HttpResponseForbidden(str(exc))` as today. Compare with `is not None`; don't rely on a response's truthiness.

The canceled message stays view-owned. "… before your quiz was submitted." is a submission message, not the reason's message.

Leave every `completed_at` check where it is (option A). Leave the `deadline` lines alone; Slice 22 owns them.

## Out of scope

- Completion as review access (option B).
- Harmonising 404 and 403 across views. Slice 12 kept them.
- Templates. `assignment_detail.html` and `quiz_unavailable.html` keep their `waived_at` and `ending` checks.
- Archived visibility. `learner_access` still ignores `archived_at`, as Slice 12 noted.
- The deadline label on the quiz result page ([Slice 22](22-assignment-status.md)).
- `my_training`'s section split.

## Tests

Add `training/test_learner_review_access.py`.

1. **Write this test first, against today's code, and see it pass.** `LearnerResponseMatrixTests` is one table-driven HTTP test of the Problem matrix. For each row, build a fresh assignment with one acknowledged slide and one failed attempt, then end it. Each cell asserts the status code and the message, or the redirect target. Request the cells in this order: detail, slide, acknowledge, `take_quiz`, `quiz_result` for the failed attempt, then `submit_quiz_view` with no answers, with a new submission ID, and (completed row only) with the passing submission replayed. It must still pass, unchanged, after the refactor.
2. `LearnerAccessTests.test_level_and_message_by_ending_caller_activity_and_archiving` calls `learner_access` directly over a table of:
   - ending: none, completed, canceled, waived (set with `Assignment.objects.filter(...).update(...)`);
   - caller: the owner, another employee;
   - the caller is active or inactive;
   - archived or not.

   Expected:

   | Case | `denial` | `level` | `message` |
   |---|---|---|---|
   | Another employee, any ending or activity | `NOT_YOURS` | `NONE` | "This assignment is not available to you." |
   | Owner, inactive | `INACTIVE` | `NONE` | "This assignment is not available to you." |
   | Owner, active, no ending | `None` | `ACT` | `None` |
   | Owner, active, completed | `None` | `ACT` | `None` |
   | Owner, active, canceled | `CANCELED` | `NONE` | "This assignment was canceled." |
   | Owner, active, waived | `WAIVED` | `REVIEW` | "This assignment was waived." |

   Archiving changes no row. Also assert `can_review` and `can_act` for each row. Add `MISSING` for an unknown UUID and for `"not-a-uuid"`, with level `NONE` and message `None`.
3. `test_owned_assignment_raises_the_access_message`: `acknowledge_slide` on a waived assignment raises `AssignmentAccessError("This assignment was waived.")`. With an unknown ID, it raises `Assignment.DoesNotExist`.
4. Races, using `patch` around the domain call so the assignment is waived after the view's unlocked check:
   - `acknowledge` returns 403 "This assignment was waived.";
   - `submit_quiz_view` renders `quiz_unavailable.html` with "This assignment was waived." and 403, and records no attempt.

Don't edit any existing test.

## Done when

- The test command passes: **187 tests plus the new ones, OK (skipped=3)**.
- `makemigrations --check --dry-run` prints "No changes detected".
- These searches give the expected results:

  ```bash
  grep -n "LearnerDenial.WAIVED" training/*_views.py                            # no matches
  grep -n "This assignment was waived." training/*.py | grep -v test_           # assignments.py only, once
  grep -n "This assignment is not available to you.\|\"This assignment was canceled.\"" training/*_views.py  # no matches
  grep -n "denial\|LearnerDenial" training/assignment_views.py                  # no matches
  grep -n "access.can_review\|access.can_act" training/*_views.py               # detail, slide, acknowledge, take_quiz, quiz_result
  ```
