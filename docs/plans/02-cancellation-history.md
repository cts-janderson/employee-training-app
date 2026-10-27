# Slice 2: shared cancellation history

Part of [Deepen the training modules](deepen-modules.md).

## Problem

`cancel_assignment` and `archive_assignment` in [assignments.py](../../training/assignments.py) build the same `CANCELED` `AssignmentHistory` row field by field.

## Change

Add a private `_record_cancellation(assignment, *, actor, reason, now)` to [assignments.py](../../training/assignments.py). It:
- creates the `CANCELED` history row with the same fields as today;
- sets `assignment.canceled_at = now` without saving;
- returns the history row.

Both functions call it. Each keeps its own `save(update_fields=...)` call and its current return value. `cancel_assignment` returns `(assignment, history)` and `archive_assignment` returns `assignment`.

## Tests

None needed. The existing cancel and archive tests in [test_assignments.py](../../training/test_assignments.py), [test_assignment_admin.py](../../training/test_assignment_admin.py) and [test_assignment_archive.py](../../training/test_assignment_archive.py) cover this.
