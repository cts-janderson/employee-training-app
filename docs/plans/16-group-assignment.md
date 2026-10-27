# Slice 16: assign training by group (US-14)

Part of [Extended user stories](extended-stories.md).

## Story

[US-14](../user-stories.md#us-14--assign-training-by-group): administrators maintain employee groups and assign a published version to a group. Membership is resolved when the administrator submits, and every resolved recipient gets an outcome.

## Problem

`create_assignments` in [assignments.py](../../training/assignments.py) has one way to say who gets the assignment: `employee_ids`, at most 100, posted by the browser. Its idempotency fingerprint (`_fingerprint`) is a hash of those IDs. For a group, the browser posts a group ID and the server resolves the employees inside the transaction. Membership can change between the review and the submission. A fingerprint over resolved IDs would then reject a retry of the same submission as "used for a different request". Slice 17 (renewal) has the same need, with a third source of recipients.

So this slice adds a **recipient source** to `create_assignments`. The individual list and the group are its first two adapters. That makes it a real seam. A seam with only one adapter would be hypothetical.

## Decision

**Decision (2026-10-27): A.** Keep 100 for every source.

The assignment limit today is "one to 100 distinct employees".

- **A. Keep 100 for every source (recommended first).** Review rejects a group that resolves to more than 100 members, with a message that says so. Every assignment then still fits in one short transaction, which matters because each one queues its notification intents in that transaction.
- **B. Raise the limit for groups**, for example to 500. Check the transaction time against local PostgreSQL with 500 synthetic employees before you commit to it, and report the timing.

## Change

### 1. Glossary

Already added to [GLOSSARY.md](../GLOSSARY.md) on 2026-10-27. Adjust it only if implementation sharpens the definition:

> **Employee group**:
> A named set of employees that administrators maintain to select assignment recipients. Membership alone creates, changes, or cancels no assignment.
> _Avoid_: Team, department, role

### 2. Models (one migration)

```python
class EmployeeGroup(models.Model):
    id = UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = CharField(max_length=200)
    created_at = DateTimeField()
    # UniqueConstraint(Lower("name"), name="employee_group_name_ci_unique")

class EmployeeGroupMembership(models.Model):
    group = ForeignKey(EmployeeGroup, PROTECT, related_name="memberships")
    employee = ForeignKey(Employee, PROTECT, related_name="group_memberships")
    added_at = DateTimeField()
    # UniqueConstraint(fields=["group", "employee"])

class EmployeeGroupChange(models.Model):   # immutable, like EmployeeActivityChange
    group = ForeignKey(EmployeeGroup, PROTECT, related_name="changes")
    actor = ForeignKey(Employee, PROTECT, related_name="group_changes")
    kind = CharField(choices=CREATED | RENAMED | MEMBER_ADDED | MEMBER_REMOVED)
    employee = ForeignKey(Employee, PROTECT, null=True, blank=True, related_name="+")
    old_name = CharField(blank=True); new_name = CharField(blank=True)
    occurred_at = DateTimeField()
```

Set the timestamp fields from an injected `now`. Don't use `auto_now_add`, which ignores the injected clock (a defect listed in [round 2](deepen-modules-round-2.md#known-defects-these-slices-dont-cover)).

Extend `AssignmentOperation`:

```python
class Source(TextChoices): EMPLOYEES = "employees"; GROUP = "group"
source = CharField(max_length=20, choices=Source.choices, default=Source.EMPLOYEES)
group = ForeignKey(EmployeeGroup, PROTECT, null=True, blank=True, related_name="assignment_operations")
# CheckConstraint: group is set if and only if source == "group"
```

Existing operations get `source="employees"` from the default.

### 3. Group maintenance: a new `groups.py` module

```python
def create_group(*, name, actor, now=None) -> EmployeeGroup
def rename_group(*, group_id, name, actor, now=None) -> EmployeeGroup
def set_group_members(*, group_id, employee_ids, actor, now=None) -> EmployeeGroup
def group_members(group) -> list[Employee]          # ordered by name
```

- Each function checks `is_administrator(actor)` itself. Don't repeat the `update_demo_employee` gap noted in round 2.
- Names are trimmed, 1–200 characters and unique ignoring case. Errors are `GroupInputError(message, target=...)`, following [Slice 10](10-structured-draft-problems.md)'s targeted problems.
- `set_group_members` locks the group row with `select_for_update`, then computes what to add and remove. **Only active demo employees can be added.** An employee who is already a member and is now inactive may stay, so deactivation never silently changes a group. They can be removed. Write one `EmployeeGroupChange` for each addition and each removal.

### 4. Recipient source in `create_assignments`

In [assignments.py](../../training/assignments.py):

```python
@dataclass(frozen=True)
class Recipients:
    source: str                       # AssignmentOperation.Source
    employee_ids: tuple = ()          # for EMPLOYEES
    group_id: uuid.UUID | None = None # for GROUP

    @classmethod
    def employees(cls, ids): ...
    @classmethod
    def group(cls, group_id): ...

def resolve_recipients(recipients, *, lock=False) -> list[Employee]:
    """Return the employees to plan for. EMPLOYEES: today's load_assignment_employees.
    GROUP: lock the group row when lock=True, then load its members. Enforce MAX_RECIPIENTS
    (100, the decision above) and reject an empty group with "This group has no members." """

def create_assignments(*, operation_id, actor, version_id, recipients, due_date_value, now=None): ...
```

- `_fingerprint` hashes `[actor, version, due_date, source, sorted employee_ids]` for `EMPLOYEES`, and `[actor, version, due_date, source, group_id]` for `GROUP`. The payload for `EMPLOYEES` must hash to the same value as today. Check this with a test against a fingerprint computed by today's code, so stored operations stay idempotent after deployment.
- After the idempotency check and `validate_new_due_date`, call `resolve_recipients(recipients, lock=True)`. Lock order: **group row, then employee rows in `pk` order, then assignments.** Group maintenance takes the group lock first too, so a membership change waits for a submission in progress, or the other way round. Either way, submission sees a consistent membership.
- The per-employee loop is unchanged: each employee gets a fresh outstanding read, `_plan_one`, the savepoint and the `IntegrityError` fallback. Every resolved recipient gets an `AssignmentOperationResult`. That gives "reports an outcome for every resolved recipient", and the same `INACTIVE`, `ALREADY_ASSIGNED` and `CONFLICT` outcomes as individual assignment.
- A retry with the same `operation_id` returns the stored results, even if the membership has changed since. That's the "resolved when the administrator submits" rule.
- `parse_assignment_request` keeps its "one to 100 distinct employees" check for `EMPLOYEES` only.

### 5. Views and templates

- Add `groups/`, `groups/new/` and `groups/<uuid>/` pages, as admin-only views in a new `group_views.py`. A group page has a rename form and a membership form. The membership form is a checkbox list of active demo employees, plus current members who are inactive (labelled "Inactive"). Show the group's change history underneath.
- On the assign page ([assign.html](../../templates/training/assign.html)), add a fieldset that chooses **Employees** or **Group**. The group choice is a `<select>` of groups with their member counts. Without JavaScript, both controls show and the radio button decides which one counts.
- `_assignment_review` resolves recipients with `resolve_recipients(recipients)` (unlocked) and shows the group name, "Membership is read again when you submit", and the existing status for each employee.
- Add a link to Groups in the admin navigation.

## Tests

Domain tests (`training/test_groups.py`):

- `test_only_active_employees_can_be_added_to_a_group`.
- `test_deactivated_member_stays_in_group_until_removed`.
- `test_group_names_are_unique_ignoring_case`.
- `test_group_changes_record_actor_and_time`.
- `test_group_assignment_reports_an_outcome_for_every_member`, with one member created, one already assigned, one in conflict and one inactive.
- `test_group_assignment_uses_membership_at_submission_not_review`.
- `test_retrying_group_submission_returns_original_results_after_membership_changes`.
- `test_later_membership_changes_do_not_alter_assignments`.
- `test_group_assignment_queues_each_assignment_email`.
- `test_employee_list_fingerprint_is_unchanged`.
- `test_empty_and_oversized_groups_are_rejected`.

PostgreSQL only (skip on SQLite, like the existing concurrency tests):

- `test_membership_change_waits_for_group_submission`.

HTTP: `test_assign_page_reviews_and_submits_a_group`, and `test_employees_cannot_manage_groups`.

## Out of scope

- Automatic assignment when someone joins a group. The story rules it out.
- Nested groups and groups synced from Entra ID.
- Deleting a group. Groups are referenced by operations, so add archiving later if it's needed.
