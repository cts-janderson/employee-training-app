# Slice 9: assignment queries and labels

Part of [Deepen the training modules: round 2](deepen-modules-round-2.md). Do [Slice 7](07-follow-up-summary.md) first, because it rewrites part of the dashboard.

## Problem

The views write assignment state, labels and the clock themselves:

| Rule | Copies |
|---|---|
| State filters for pending, completed and canceled | Written inline in the ORM in `assignment_dashboard`, re-deriving `assignment_state` |
| `PROGRESS_LABELS` and `DEADLINE_LABELS` | [assignment_admin_views.py](../../training/assignment_admin_views.py), [assignment_views.py](../../training/assignment_views.py), and an inline dict in `quiz_result` in [quiz_learner_views.py](../../training/quiz_learner_views.py) |
| Dashboard sort order | Compares display labels: `row["state"] == "Pending"`, `row["deadline"] == "Overdue"` |
| Learner's visible assignments | Written inline in `my_training`: `canceled_at__isnull=True, archived_at__isnull=True` |
| Clock | `deadline_status(assignment)` is called without `now` in `_detail_context`, `assignment_detail` and `quiz_result` |

## Change

### 1. Queryset methods

Add these to `AssignmentQuerySet` in [models.py](../../training/models.py):

```python
def in_state(self, state):
    """Filter by assignment_state(): "pending", "completed" or "canceled"."""

def visible_to_learner(self, employee):
    return self.filter(employee=employee, canceled_at__isnull=True, archived_at__isnull=True)
```

- `in_state("pending")` is `outstanding()`.
- `in_state("completed")` is `filter(completed_at__isnull=False, canceled_at__isnull=True)`.
- `in_state("canceled")` is `filter(canceled_at__isnull=False)`.
- An unknown state raises `ValueError`.

Add a one-line comment saying `in_state` mirrors `assignment_state` in [assignments.py](../../training/assignments.py).

Then:
- `assignment_dashboard` uses `in_state(...)` for the three states. Leave the archived filter and the `"all"` value as they are.
- `my_training` uses `Assignment.objects.visible_to_learner(request.employee)`.

### 2. One label map

Move `PROGRESS_LABELS` and `DEADLINE_LABELS` to [assignments.py](../../training/assignments.py), next to `progress_status` and `deadline_status`. Import them in both assignment view modules and in `quiz_result`. Delete the three copies.

### 3. Sort on codes, not labels

In `assignment_dashboard`, store the sort rank as each row is built, from the `state` and `deadline` codes:
- 0: pending and overdue
- 1: pending
- 2: completed
- 3: anything else

Don't add the key to the template context: sort first, then drop it, or sort a list of `(rank, row)` pairs. Archived rows keep today's behaviour. Their label starts with "Archived ·", so today they always rank 3. Keep that.

### 4. One clock per request

Pass the request's `now` to `deadline_status` in:
- `_detail_context`, which already computes `now`;
- `assignment_detail`, where you add `now = timezone.now()`;
- `quiz_result`, where you add `now = timezone.now()`.

## Out of scope

- The `get_object_or_404(..., canceled_at__isnull=True)` access checks in the learner views. [Slice 12](12-learner-access.md) covers them.
- Moving the progress and deadline filters into SQL. They depend on `now` and stay in Python.
- The latest-published-title copies in `training_list`, the dashboard's training filter and `assign_training`. Each is a one-line expression over versions already in `version_number` order (`TrainingVersion.Meta.ordering`). A helper would fail the deletion test.

## Tests

Add these to `AssignmentLifecycleTests` in [test_assignments.py](../../training/test_assignments.py):
- For each state, `in_state` returns the same assignments as filtering in Python with `assignment_state`. Cover a pending, a completed, a canceled, and a completed-then-canceled assignment.
- `in_state("bogus")` raises `ValueError`.
- `visible_to_learner` excludes canceled assignments, archived ones, and other employees' assignments.

The existing dashboard tests cover the sort order. Run them, and don't change them.

## Done when

- The test command passes.
- `grep -n "PROGRESS_LABELS = \|DEADLINE_LABELS = \|\"Completed late\"" training/*.py` matches only `assignments.py`.
- `grep -n "deadline_status(assignment)" training/*.py` finds no matches.
