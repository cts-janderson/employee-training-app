# Slice 18: assignment endings

Part of [Extended user stories](extended-stories.md). A pure refactor that [Slice 19](19-waivers.md) builds on.

## Problem

An assignment ends by **completion** or **cancellation**. A waiver (`US-18`) would be a third way. Today, "has this assignment ended, and how?" is answered by reading `completed_at` and `canceled_at` directly, in all of these places:

| Where | What it decides |
|---|---|
| `AssignmentQuerySet.outstanding()`, `in_state()` and `visible_to_learner()` in [models.py](../../training/models.py) | Which assignments are outstanding, completed or canceled, and which ones learners see |
| The `one_pending_assignment_per_employee_training` constraint condition | ADR-0001's one outstanding assignment |
| `assignment_state()` in [assignments.py](../../training/assignments.py) | The state label |
| `change_assignment_deadline` and `cancel_assignment` guards; `archive_assignment`'s `canceling` | Whether a mutation is allowed |
| `learner_access` | `LearnerDenial.CANCELED` |
| `manual_nudge_problem`, `assignment_ended` and `_suppression_reason` in [notifications.py](../../training/notifications.py) | Follow-up eligibility and suppression reasons |
| Slice 13's SQL annotations in [assignment_reports.py](../../training/assignment_reports.py) | Report state and rank |
| `quiz_unavailable.html`, `assignment_admin_detail.html` and others | What the page says |

Each place encodes the rule its own way: `completed_at or canceled_at`, `not completed_at and not canceled_at`, `canceled_at` checked before `completed_at`. Adding `waived_at` would mean editing every row of that table. Missing one row would let a waived assignment keep sending reminders or block a replacement.

## Change

### 1. One definition on the model

In [models.py](../../training/models.py):

```python
class Ending(models.TextChoices):
    COMPLETED = "completed"
    CANCELED = "canceled"

OUTSTANDING = Q(completed_at__isnull=True, canceled_at__isnull=True)

def _ending_expression():
    """Case(When(canceled_at set → CANCELED), When(completed_at set → COMPLETED), default None)."""

class AssignmentQuerySet(models.QuerySet):
    def outstanding(self):        return self.filter(OUTSTANDING)
    def with_ending(self):        return self.annotate(ending=_ending_expression())
    def ended_by(self, ending):   return self.with_ending().filter(ending=ending)
    def in_state(self, state):    # pending → outstanding(); completed/canceled → ended_by(...)
    def visible_to_learner(self, employee):
        return self.filter(employee=employee, canceled_at__isnull=True, archived_at__isnull=True)  # unchanged

class Assignment(models.Model):
    @property
    def ending(self) -> str | None:
        """CANCELED, COMPLETED or None, in the same precedence as _ending_expression()."""
    @property
    def is_outstanding(self) -> bool:
        return self.ending is None
    # Meta: the unique constraint uses condition=OUTSTANDING
```

- Using `OUTSTANDING` in the constraint produces the same `Q`. Check that `makemigrations --check` reports no changes.
- Precedence: cancellation comes before completion, matching today's `assignment_state()`. The two can't both be set, but the order must be the same in Python and SQL.
- The `ending` annotation name would clash with the property on annotated querysets. Annotate it as `ending_value` and read it only inside the queryset methods. Keep the property as the per-object interface.
- Keep `visible_to_learner` excluding canceled assignments **by name**. Learner visibility is a separate rule from ending. Slice 19 makes waived work visible.

### 2. Callers use the definition

- `assignment_state(assignment)` returns `assignment.ending or "pending"`.
- Add a private `_require_outstanding(assignment, message)` in [assignments.py](../../training/assignments.py) that raises `AssignmentConflict(message)`. Use it in `change_assignment_deadline` and `cancel_assignment`, with today's messages. `archive_assignment` uses `canceling = assignment.is_outstanding`.
- `learner_access` returns `LearnerDenial.CANCELED` when `assignment.ending == Ending.CANCELED`.
- In [notifications.py](../../training/notifications.py):
  - `manual_nudge_problem` uses `not assignment.is_outstanding`.
  - `assignment_ended` and `_suppression_reason` use `f"assignment_{assignment.ending}"`. The stored reasons stay `assignment_completed` and `assignment_canceled`.
  - `notifications.py` imports only from `models`, so there's no import cycle with `assignments.py`.
- Slice 13's report annotations use `with_ending()` for state and rank, instead of their own `Case` on the timestamps.
- In templates, replace `completed_at and not canceled_at` with `assignment.ending == "completed"`. Keep `{{ assignment.completed_at }}` and `{{ assignment.canceled_at }}` wherever a page **shows** the time.

`_complete_if_satisfied` and `submit_quiz` still check `completed_at` directly. They ask "is it already complete?", not "has it ended?". A canceled assignment can't reach them because of `learner_access`. Leave them unchanged.

## Tests

Add these to `training/test_assignment_endings.py`:

- `test_ending_property_matches_queryset_annotation`, for not started, in progress, completed, canceled, and archived after cancellation.
- `test_outstanding_matches_unique_constraint_condition`: create a second assignment for an employee whose first assignment is completed (allowed) and one whose first is pending (`IntegrityError`).
- `test_suppression_reasons_are_unchanged`.

All existing tests must pass unchanged.

## Verification

```bash
grep -n "completed_at or assignment.canceled_at\|completed_at and not assignment.canceled_at" training/*.py  # no matches
grep -n "canceled_at__isnull" training/*.py | grep -v models.py                                              # no matches
grep -n "canceled_at\|completed_at" training/notifications.py                                                # no matches
```
