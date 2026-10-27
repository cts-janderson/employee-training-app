# Slice 17: renew required training (US-15)

Part of [Extended user stories](extended-stories.md). It depends on [Slice 16](16-group-assignment.md). The decisions are recorded in [ADR-0005](../adr/0005-renewal-interval-on-training.md).

## Story

[US-15](../user-stories.md#us-15--renew-required-training): when a completed requirement reaches its renewal date, an administrator reviews and creates a new assignment for the next cycle. Earlier evidence is kept, conflicts are reported, and both administrators and employees can tell the current cycle from earlier ones.

## Problem

Renewal is a third recipient source for `create_assignments`, after the individual list and the group from Slice 16. Its recipients are "employees whose latest completion of this training has reached its renewal date". Each new assignment also has to remember which completed assignment it renews. Slice 16's `resolve_recipients` returns plain `Employee`s, so this slice widens what it returns. It doesn't add a parallel creation path. A second creation function would duplicate the idempotency, locking, `_plan_one` and notification steps.

[ADR-0001](../adr/0001-one-outstanding-assignment-per-training.md) still applies. A renewal never creates a second outstanding assignment. An employee who already has one, for example because they were reassigned by hand, is reported as `CONFLICT` or `ALREADY_ASSIGNED`.

## Decisions

**Decision 1 (renewal date), 2026-10-27: A.**

- **A. A renewal interval on the training (recommended).** Add `Training.renewal_interval_months`, a nullable positive integer edited on the draft editor page. It's stored on the logical training, not the version, so a revision doesn't silently change the cycle. A completion's **renewal date** is the calendar date of `completed_at` in the organization time zone plus that many months, clamped to the end of the month. Recorded as [ADR-0005](../adr/0005-renewal-interval-on-training.md), because the [deferred scope](../implementation-plan.md#deferred-scope) avoids configurable policies, and this is deliberately the smallest one.
- **B. No stored policy.** On each renewal review, the administrator enters a cutoff date: "completed on or before". This is simpler, but there's no renewal date to show employees, and the result depends on the administrator remembering the cycle.

**Decision 2 (who triggers it), 2026-10-27: A.**

- **A. The administrator starts it (recommended).** Renewals are created only from the review page. This meets "review the employees and due dates before new assignments are created", and avoids the deferred "automatic reassignment".
- **B. A worker queues renewals automatically.** Defer this. It needs an approval step that the story doesn't describe.

The rest of this plan follows **1A and 2A**.

## Change

### 1. Glossary

Already added to [GLOSSARY.md](../GLOSSARY.md) on 2026-10-27. Adjust it only if implementation sharpens the definition:

> **Renewal**:
> A new assignment of a training, created after an employee's completion of it reaches its renewal date, that begins the employee's next cycle without altering earlier assignments.
> _Avoid_: Reassignment, retake, extension
>
> **Renewal date**:
> The date on which a completed assignment's training becomes due for renewal.
> _Avoid_: Due date, expiry

### 2. Models (one migration)

```python
class Training:
    renewal_interval_months = PositiveSmallIntegerField(null=True, blank=True)  # 1–120 when set

class Assignment:
    renews = ForeignKey("self", PROTECT, null=True, blank=True, related_name="renewals")
    cycle_number = PositiveIntegerField(default=1)
    # UniqueConstraint(fields=["renews"], condition=Q(renews__isnull=False, canceled_at__isnull=True),
    #                  name="one_live_renewal_per_completion")

class AssignmentOperation.Source:  RENEWAL = "renewal"
AssignmentOperation.renewal_as_of = DateField(null=True, blank=True)
# extend the CheckConstraint: renewal_as_of is set if and only if source == "renewal"
```

Existing assignments get `cycle_number=1` from the default. `cycle_number` is set when the assignment is created (`renews.cycle_number + 1`) and never changes afterwards. That way the cycle is a stored fact, not something worked out from a chain of rows on every page view. A canceled renewal frees its prior completion to be renewed again, which the partial unique constraint allows.

Slice 18 later replaces `canceled_at__isnull=True` in this constraint with its single ended-state condition. Leave a comment there.

### 3. Renewal rules: a new `renewals.py` module

```python
def renewal_date(assignment) -> date | None:
    """completed_at's organization-time-zone date plus training.renewal_interval_months,
    clamped to the month's last day. None if the training has no interval or the assignment isn't completed."""

def renewal_candidates(training, *, as_of) -> list[RenewalCandidate]:
    """For each demo employee, take their latest completed assignment of `training` that isn't archived.
    Return it if its renewal_date is on or before `as_of` and no later assignment of the training exists for that employee."""
```

`RenewalCandidate(employee, completed_assignment, renewal_date)`. Archived completed work isn't renewed. If an administrator wants it renewed, they restore it first. The "no later assignment" rule means a completion is renewed once, even if its renewal was itself completed.

### 4. Widen the recipient source

In [assignments.py](../../training/assignments.py):

- Add `Recipients.renewal(training_id, as_of)`.
- Change `resolve_recipients` to return `[ResolvedRecipient(employee, renews=None)]`. The `EMPLOYEES` and `GROUP` sources wrap their employees. `RENEWAL` calls `renewal_candidates` and sets `renews`. With `lock=True`, it locks the employees in `pk` order, then each employee's candidate assignment.
- Include `"renewal"`, `training_id` and `as_of` in the fingerprint for this source.
- In the creation loop, pass `renews` and `cycle_number` to `Assignment.objects.create`. If `IntegrityError` is raised on `one_live_renewal_per_completion`, re-plan that employee as `ALREADY_ASSIGNED`. A concurrent renewal won.
- The version must belong to the renewed training. `create_assignments` already ties the training to the version.
- Recipients that resolve to nothing produce "No completed assignments are due for renewal on or before this date." as an `AssignmentInputError`.

Due dates: one due date per renewal operation, chosen on the review page, as for every other source. The review shows each employee's renewal date beside the shared due date, so the administrator can see how much notice they're giving.

### 5. Views and templates

- Add `trainings/<uuid>/renew/` (admin only). It follows the same review → submit → results steps as the assign page, with the version (latest published by default), the **renewal date on or before** date (default today) and the due date. Reuse `_assignment_review` with `Recipients.renewal(...)`. The review rows add "Completed <date>" and "Renewal date <date>".
- On the draft editor, add a "Renewal interval (months)" field with help text: "Leave blank if this training is not renewed."
- **Admin assignment detail:** show "Cycle N". Show "Renews <link>" when `renews` is set, and "Renewed by <link>" for each renewal.
- **Dashboard (Slice 13):** add `cycle_number` to `ReportRow` and a **Cycle** column. Add **Cycle** to Slice 14's export columns, after **Version**.
- **My training:** an assignment with `cycle_number > 1` shows "Renewal · cycle N" next to its title. In the completed list, each completed cycle stays as its own row, labelled "Cycle N · completed <date>", so the current cycle and earlier cycles look different.

## Tests

`training/test_renewals.py`:

- `test_renewal_date_adds_interval_in_organization_time_zone_and_clamps_month_end`, for example completed 2026-01-31 + 1 month = 2026-02-28.
- `test_candidates_exclude_employees_with_a_later_assignment`.
- `test_candidates_exclude_archived_and_canceled_completions`.
- `test_renewal_creates_new_assignment_and_preserves_prior_evidence`: the prior assignment's attempts, acknowledgments and `completed_at` are unchanged.
- `test_renewal_reports_conflict_when_employee_already_has_outstanding_assignment`.
- `test_renewal_increments_cycle_number_and_links_prior_assignment`.
- `test_renewal_uses_existing_due_date_and_assignment_email_rules`.
- `test_retrying_renewal_submission_returns_original_results`.
- `test_canceled_renewal_can_be_renewed_again`.
- `test_learner_sees_current_cycle_and_prior_completed_cycles`.

PostgreSQL only: `test_concurrent_renewals_of_one_completion_create_one_assignment`.

## Out of scope

- Automatic or scheduled renewal (Decision 2B).
- A separate due-date offset for each employee.
- Expiring a completion. A completion past its renewal date stays a completion. Reports show the new outstanding cycle.
