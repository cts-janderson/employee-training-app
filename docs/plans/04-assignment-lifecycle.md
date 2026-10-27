# Slice 4: assignment lifecycle

Part of [Deepen the training modules](deepen-modules.md).

## Problem

Rules about an assignment's state are spread across the codebase:

| Rule | Current copies |
|---|---|
| Outstanding filter `completed_at__isnull=True, canceled_at__isnull=True` | [assignments.py](../../training/assignments.py) (twice), `_assignment_review` in [assignment_views.py](../../training/assignment_views.py), the dashboard "pending" filter in [assignment_admin_views.py](../../training/assignment_admin_views.py), `_unavailable_quiz` in [quiz_learner_views.py](../../training/quiz_learner_views.py) |
| State label | `_assignment_state` in [assignment_admin_views.py](../../training/assignment_admin_views.py); `progress_status` and `deadline_status` in [assignments.py](../../training/assignments.py) |
| All slides acknowledged | `acknowledge_slide`, `submit_quiz` ([quizzes.py](../../training/quizzes.py)), `take_quiz` ([quiz_learner_views.py](../../training/quiz_learner_views.py)); also `quiz_ready` in `assignment_detail`, in memory |
| Completion rule | split between `acknowledge_slide` (all slides acknowledged and no quiz) and `submit_quiz` (passing attempt) |
| Next step | `_learner_next_action` in [assignment_views.py](../../training/assignment_views.py) mixes the rule with `reverse()` |

## Change

### 1. Queryset method

In [models.py](../../training/models.py), add:

```python
class AssignmentQuerySet(models.QuerySet):
    def outstanding(self):
        return self.filter(completed_at__isnull=True, canceled_at__isnull=True)
```

Set `objects = AssignmentQuerySet.as_manager()` on `Assignment`.

Replace every copy listed in the table with `.outstanding()`. For example, `Assignment.objects.get(employee=..., training=..., completed_at__isnull=True, canceled_at__isnull=True)` becomes `Assignment.objects.outstanding().get(employee=..., training=...)`.

Leave the `UniqueConstraint` condition in `Meta` as a literal `Q`. Migrations depend on it.

### 2. State in the domain module

Move `_assignment_state` from [assignment_admin_views.py](../../training/assignment_admin_views.py) to [assignments.py](../../training/assignments.py) as the public `assignment_state(assignment)`, next to `progress_status` and `deadline_status`. Update the view's imports and calls.

### 3. Slide completion

Add `slides_acknowledged(assignment)` to [assignments.py](../../training/assignments.py). It returns `assignment.slide_progress.count() == assignment.version.slides.count()`.

- Use it in `acknowledge_slide`, `submit_quiz` and `take_quiz`.
- In `assignment_detail`, keep `len(acknowledged_ids) == len(slides)`, because the view already holds both sets. Add a one-line comment saying it's the same rule.

### 4. One completion rule

Add a private `_complete_if_satisfied(assignment, *, now, passed_quiz=False)` to [assignments.py](../../training/assignments.py). It sets `completed_at = now`, without saving, when either:
- the training version has no quiz and all slides are acknowledged, or
- `passed_quiz` is true.

It returns whether the assignment became complete.

- Keep the current short-circuit: check the slides before running `Quiz.objects.filter(version=...).exists()`.
- Never overwrite an existing `completed_at`. If it's already set, return false.
- `acknowledge_slide` calls it before its existing save, whose `update_fields` already includes `completed_at`.
- `submit_quiz` calls `_complete_if_satisfied(assignment, now=now, passed_quiz=passed)` and saves `completed_at` only when that returns true. [quizzes.py](../../training/quizzes.py) already imports `_owned_assignment` from `.assignments`; import this the same way.
- Keep the slides guard in `submit_quiz` where it is. It must still raise "Acknowledge every slide before submitting the quiz." before an attempt is created.

### 5. Next step as a domain decision

Move the rule in `_learner_next_action` into [assignments.py](../../training/assignments.py) as `learner_next_step(assignment, slides, acknowledged_ids, has_quiz)`. It returns one of:
- `("review", None)`
- `("start", slide)`
- `("resume", slide)`
- `("take_quiz", None)`

Keep `_learner_next_action` in [assignment_views.py](../../training/assignment_views.py) as a thin adapter that maps the step to the existing `{"label", "url", "target"}` dict using `reverse()`. The labels must stay "Review", "Start", "Resume" and "Take quiz", because `acknowledge` compares `next_action["label"] == "Resume"`.

## Out of scope

- The views' `get_object_or_404(..., canceled_at__isnull=True)` checks before they call `_owned_assignment`. Those checks decide between 404, 403 and 409 responses.

## Tests

Add `AssignmentLifecycleTests` to [test_assignments.py](../../training/test_assignments.py). If you need that file's fixtures, move them into a mixin.

- `outstanding()` excludes completed and canceled assignments.
- `assignment_state` returns canceled, completed or pending. Canceled wins when both timestamps are set.
- `learner_next_step`:
  - a completed assignment returns review;
  - an unstarted one returns start on the first slide;
  - a started one returns resume on the saved slide, or on the first unacknowledged slide if the saved one is already acknowledged;
  - all slides acknowledged with a quiz returns take_quiz.
- Completion: acknowledging the last slide completes an assignment with no quiz, but not one with a quiz. A passing attempt completes the assignment and a failing one doesn't. Read [test_assignments.py](../../training/test_assignments.py) and [test_quizzes.py](../../training/test_quizzes.py) first, and add only the cases that aren't already covered.

## Done when

- The test command passes.
- `makemigrations --check --dry-run` prints "No changes detected".
- The outstanding-filter search in [deepen-modules.md](deepen-modules.md#final-verification) matches only `models.py`.
