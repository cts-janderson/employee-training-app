# Slice 12: learner access

Part of [Deepen the training modules: round 2](deepen-modules-round-2.md). **Make the decision below before you start.**

## Problem

Each learner view decides access before the domain does, and the views give different responses to the same situation:

| View | Missing | Another employee's | Canceled |
|---|---|---|---|
| `assignment_detail` | 404 | 404 | 404 |
| `assignment_slide` | 404 | 404 | 404 |
| `acknowledge` | 404 | 404 | 404 |
| `take_quiz` | 404 | 404 | **409** `quiz_unavailable.html` |
| `submit_quiz_view` | 404 | **403** | **403** `quiz_unavailable.html` |
| `quiz_result` | 404 | 404 | 404 |

The views are in [assignment_views.py](../../training/assignment_views.py) and [quiz_learner_views.py](../../training/quiz_learner_views.py).

The domain then re-checks access in `_owned_assignment` ([assignments.py](../../training/assignments.py)) and raises `AssignmentAccessError` with its own messages. `submit_quiz_view` has to look the canceled state up again in its `except AssignmentAccessError` branch to tell the two cases apart. No single place answers "may this employee open this assignment, and if not, why not?".

## Decision needed

**Decision: Option A.** Preserve the existing HTTP responses while moving the access decision into the domain.

Pick one option before starting. Record it in this file, and as an ADR if it's option B.

- **A. Keep today's responses (recommended first step).** The access rule moves into one domain function, but each view keeps its own mapping from a denial to a response. This is a pure refactor: no test changes.
- **B. Harmonise.** For example, 404 for missing or another employee's assignment everywhere, so learners can't probe for assignment IDs, and the `quiz_unavailable.html` page for canceled assignments everywhere. This changes `test_quizzes.py`, which asserts 403 for another employee's submission, and some `test_assignments.py` 404 cases. List each changed assertion in the slice report.

Option A gets the locality now and makes option B a one-line change per view later.

## Change (option A)

### 1. One access decision

Add this to [assignments.py](../../training/assignments.py):

```python
class LearnerDenial(models.TextChoices):  # or plain string constants, following the surrounding style
    MISSING = "missing"
    NOT_YOURS = "not_yours"
    INACTIVE = "inactive"
    CANCELED = "canceled"


def learner_access(assignment_id, employee, *, lock=False):
    """Return (assignment, None) when the employee may act on it, or (assignment_or_None, denial)."""
```

- Check in today's `_owned_assignment` order: not yours or inactive, then canceled.
- `MISSING` covers a missing row, and also an ID the ORM can't parse: catch `Assignment.DoesNotExist`, `ValidationError` and `ValueError` around the lookup.
- With `lock=True`, it uses `select_for_update().select_related("version")`, as `_owned_assignment` does.

`_owned_assignment` becomes a thin wrapper. It calls `learner_access(..., lock=True)` and maps each denial to exactly what it raises today:

| Denial | `_owned_assignment` raises |
|---|---|
| `MISSING` | `Assignment.DoesNotExist`, as today's `.get()` does. `submit_quiz_view` catches it. |
| `NOT_YOURS`, `INACTIVE` | `AssignmentAccessError("This assignment is not available to you.")` |
| `CANCELED` | `AssignmentAccessError("This assignment was canceled.")` |

### 2. Views map denials

Each learner view calls `learner_access(assignment_id, request.employee)` instead of `get_object_or_404(..., canceled_at__isnull=True)` or its own lookup. A small dict per view maps each denial to today's response:
- `Http404` for most views;
- `HttpResponseForbidden("This assignment is not available to you.")` for `submit_quiz_view` with `NOT_YOURS`;
- `_unavailable_quiz(...)` with today's message and status for canceled assignments in `take_quiz` and `submit_quiz_view`.

Remove the `Assignment.objects.filter(..., canceled_at__isnull=False).exists()` re-query in `submit_quiz_view`'s `except AssignmentAccessError` branch. Branch on a fresh `learner_access` call instead.

Keep each view's `select_related` and `prefetch_related` needs. `learner_access` may take a `queryset=` keyword so the views can pass theirs.

`demo_actor_required` already rejects inactive actors. `INACTIVE` mostly protects the domain functions called outside HTTP.

## Out of scope

- Admin views. They use `employee__is_demo=True` and the administrator check.
- Archived visibility. Today an archived completed assignment is still reachable by URL, and that is unchanged here.

## Tests

- `LearnerAccessTests` in [test_assignments.py](../../training/test_assignments.py), calling `learner_access` directly:
  - own active assignment: allowed;
  - another employee's: `NOT_YOURS`;
  - inactive employee: `INACTIVE`;
  - canceled: `CANCELED`;
  - unknown id: `MISSING`.
  - When an assignment is both canceled and not yours, it gives `NOT_YOURS`, matching `_owned_assignment`'s order.
- `learner_access` with an unparseable ID returns `MISSING`. Each view's HTTP status and text for an unparseable ID stay as they are today; capture them before you start.
- A race: the assignment is canceled after the view's unlocked `learner_access` check, but before the domain's locked one. `acknowledge` returns 403 with "This assignment was canceled.", as today. `submit_quiz_view` renders the canceled `quiz_unavailable.html` page with 403.
- Add one table-driven HTTP test that captures today's status matrix from the problem table above. It pins option A's behaviour and becomes the list of changes if option B is chosen later.

## Done when

- The test command passes.
- `grep -n "canceled_at__isnull" training/assignment_views.py training/quiz_learner_views.py` matches only `my_training`, or nothing if Slice 9 is done.
