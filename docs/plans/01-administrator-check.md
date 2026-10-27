# Slice 1: one administrator check

Part of [Deepen the training modules](deepen-modules.md). This is the only slice that changes behaviour.

## Problem

The administrator check exists three times, and the copies disagree:

- `create_assignments` in [assignments.py](../../training/assignments.py) checks `role` and `is_active` only.
- `_administrator_can_change` in [assignments.py](../../training/assignments.py) also requires `is_demo`.
- `_administrator` in [notifications.py](../../training/notifications.py) also requires `is_demo`.

So a non-demo administrator can create assignments but cannot change, cancel or nudge them.

## Change

1. Add `is_administrator(actor)` to [employees.py](../../training/employees.py). It returns `actor.role == Employee.Role.ADMINISTRATOR and actor.is_active and actor.is_demo`.
2. In [assignments.py](../../training/assignments.py):
   - Make `_administrator_can_change` call `is_administrator`.
   - Replace the inline check in `create_assignments` with `_administrator_can_change(actor)`.
3. In [notifications.py](../../training/notifications.py), make `_administrator` call `is_administrator`.
4. Each module keeps raising its own error class: `AssignmentAccessError` or `NotificationError`. The message stays "Administrator access is required."

Every existing test fixture and `bootstrap_demo_admin` already create administrators with `is_demo=True`, so no fixture changes are expected.

## Tests

Write this test first and watch it fail before making the change. In [test_assignments.py](../../training/test_assignments.py), add `test_non_demo_administrator_cannot_create_assignments`:

- An administrator with `is_demo=False` calls `create_assignments`.
- It raises `AssignmentAccessError`.
- No assignment and no `AssignmentOperation` is created.

## Out of scope

- `dev_auth.demo_admin_required`. It gates HTTP access and is a separate concern.
