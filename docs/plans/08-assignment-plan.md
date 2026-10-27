# Slice 8: assignment plan

Part of [Deepen the training modules: round 2](deepen-modules-round-2.md).

## Problem

The assignment review screen is a dry run of `create_assignments`, but it is written separately. `_assignment_review` in [assignment_views.py](../../training/assignment_views.py) re-derives the domain's rules from [assignments.py](../../training/assignments.py):

| Rule | `create_assignments` | `_assignment_review` |
|---|---|---|
| Parse the operation and employee IDs → "An assignment identifier is invalid." | yes | yes |
| One to 100 distinct employees → "Choose one to 100 distinct employees." | yes | yes |
| Load demo employees → "One or more employees were not found." | yes, locked, ordered by `pk` | yes, unlocked, ordered by `name` |
| Same obligation (same version, `due_date`, `due_timezone` and `due_at`) | **twice**: the normal path and the `IntegrityError` path | once |
| Outcome: inactive, already assigned, conflict, or ready | `AssignmentOperationResult.Outcome` and detail strings | its own status strings, kept in step by hand |

If the two copies drift apart, the review promises one outcome and the submission produces another. The only tests of the agreement go through HTTP.

## Change

### 1. One plan in `assignments.py`

Add these to [assignments.py](../../training/assignments.py):

```python
def parse_assignment_request(operation_id, employee_ids):
    """Return (operation_uuid, employee_uuids) or raise AssignmentInputError with today's messages."""

def load_assignment_employees(employee_ids, *, lock=False):
    """Return the demo employees for the IDs, or raise "One or more employees were not found."
    With lock=True, lock them in pk order (creation). Otherwise order them by name (review)."""

def plan_assignments(version, employees, *, due_date, zone_name, due_at):
    """Return [(employee, outcome, current)] in the order of `employees`.
    outcome is an AssignmentOperationResult.Outcome, or None when the assignment is ready to create.
    current is the employee's outstanding assignment for the training, or None."""

def _plan_one(employee, current, version, *, due_date, zone_name, due_at):
    """Return the outcome for one employee. Owns the same-obligation rule."""
```

- `plan_assignments` loads the outstanding assignments with one query, `Assignment.objects.outstanding().filter(employee__in=employees, training=version.training).select_related("version")`, then calls `_plan_one` for each employee. **Only the review uses it.** A batch read is a snapshot. Locking the employees doesn't stop another transaction from canceling, or changing the deadline of, an existing assignment while creation works through the list.
- `_plan_one` keeps today's order of checks: inactive, then same obligation (`ALREADY_ASSIGNED`), then any other outstanding assignment (`CONFLICT`), then `None`.

### 2. `create_assignments` decides per employee, at execution time

Keep the steps outside the plan in their current order:
1. the administrator check;
2. `parse_assignment_request` (it also parses `version_id`; leave that parse inline);
3. date parsing, the fingerprint and the version lookup;
4. the operation idempotency check;
5. `validate_new_due_date`;
6. `load_assignment_employees(..., lock=True)`.

Then:
- Keep today's loop over the locked employees. For each one, read `current` with today's per-employee query, `Assignment.objects.outstanding().filter(employee=employee, training=version.training).first()`, immediately before calling `_plan_one`. Don't call `plan_assignments` here.
- For outcome `None`, create the assignment as today, inside the nested `atomic()` savepoint with the `IntegrityError` handling. The savepoint and the partial unique constraint stay the backstop for concurrent creation.
- On `IntegrityError`, reload `current` with `.outstanding().get(...)` as today, and call `_plan_one` again instead of repeating the comparison. Today, an `IntegrityError` with no outstanding assignment behind it surfaces as `DoesNotExist`. Leave that unchanged, and see Out of scope.
- Map each outcome to today's detail string. Keep the strings in one dict next to the function:
  - "Employee is inactive."
  - "This obligation already exists."
  - "Cancel the outstanding assignment before replacing it."
  - "Assignment created."

The results are still written in employee `pk` order, as today. The early replay return for an existing operation still orders by `employee__name`. Don't touch it.

### 3. The review renders the plan

In [assignment_views.py](../../training/assignment_views.py), `_assignment_review`:
- calls `parse_assignment_request`, `validate_new_due_date`, `load_assignment_employees` (unlocked, ordered by name) and `plan_assignments`;
- maps each outcome to today's label strings, character for character. For `CONFLICT`, it uses `current.version.version_number`.

The review keeps its current return dict and its current error order:
- An invalid ID or employee count is reported before an invalid date, as today.
- The view must not create an `AssignmentOperation`.

## Out of scope

- Moving the "Choose a published version of this training." check out of `assign_training`.
- Batching the `Assignment.objects.create` calls.
- Changing the result counts or the template.
- Narrowing the `except IntegrityError` to the assignment's unique constraint. Note it as a pre-existing defect.

## Tests

Add `AssignmentPlanTests(TestCase)` to [test_assignments.py](../../training/test_assignments.py). Reuse or extract the fixtures from `AssignmentWorkflowTests` into a mixin.

- `_plan_one`, without the database where possible:
  - an inactive employee gives `INACTIVE`, even with an outstanding assignment;
  - same version, date and zone gives `ALREADY_ASSIGNED`;
  - a different version gives `CONFLICT`;
  - a different due date gives `CONFLICT`;
  - no current assignment gives `None`.
- `plan_assignments` returns rows in the order of its input and uses one query for outstanding assignments.
- `create_assignments` sees the current state: cancel an employee's outstanding assignment between the review and the submit. The submit must then create a new assignment, not report a conflict.
- Review and creation agree: for a mixed group (inactive, already assigned, conflict, ready), the review labels and the `create_assignments` outcomes match row by row.
- `parse_assignment_request` raises both messages for bad IDs and for 0, 101 and duplicate IDs.

## Done when

- The test command passes.
- `grep -n "Choose one to 100 distinct employees" training/*.py` matches only `assignments.py`.
- `grep -n "due_timezone == " training/*.py` matches only `_plan_one`.
