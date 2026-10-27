# Slice 19: record a requirement waiver (US-18)

Part of [Extended user stories](extended-stories.md). It depends on Slices [13](13-assignment-report.md), [14](14-export-training-status.md) and [18](18-assignment-endings.md). The decisions are recorded in [ADR-0006](../adr/0006-final-waivers-by-any-other-administrator.md).

## Story

[US-18](../user-stories.md#us-18--record-a-requirement-waiver): an administrator records a justified waiver for an assignment. The waiver is an outcome of its own. It isn't a completion or a cancellation, and it isn't unfinished work. It preserves all evidence and suppresses future reminders.

## Decisions

Decided on 2026-10-27 and recorded in [ADR-0006](../adr/0006-final-waivers-by-any-other-administrator.md). The alternatives are kept below for context.

**Decision 1 (who may waive): A.**

- **A. Any administrator (recommended).** The MVP has one administrator role, and the [deferred scope](../implementation-plan.md#deferred-scope) excludes complex permission hierarchies. Accountability comes from the recorded actor, reason and time.
- **B. A separate approver role.** This needs a new `Employee.Role` value and a "requested → approved" flow. It's a much larger slice.

**Decision 2 (self-waiver): A.**

- **A. An administrator can't waive their own assignment (recommended).** It costs one check, and it removes the obvious conflict of interest.
- **B. Allowed, with the same record.**

**Decision 3 (revocation): A.**

- **A. A waiver is final in this slice (recommended).** To bring the requirement back, the administrator creates a new assignment, which ADR-0001 allows once the waived one has ended. This mirrors how cancellation works.
- **B. Revocation that resumes the original obligation.** The same assignment becomes outstanding again with its old progress. That needs rules for a due date that has passed, for rescheduling reminders, and for an outstanding assignment that was created in the meantime and would now break the unique constraint. Defer this unless administrators need it.

The rest of this plan follows **1A, 2A and 3A**.

## Change

### 1. Glossary

Already added to [GLOSSARY.md](../GLOSSARY.md) on 2026-10-27. Adjust it only if implementation sharpens the definition:

> **Waiver**:
> An administrator's justified decision that an employee need not fulfill an assignment. It ends the obligation without recording completion and preserves its prior activity.
> _Avoid_: Exemption, completion, cancellation, pass

Update **Cancellation** to keep its "_Avoid_: … waiver" entry. Add **Waived assignment** where pending and actionable assignments are defined, if the definitions need it. A waived assignment is neither pending nor actionable.

### 2. Model (one migration)

```python
class Assignment:
    waived_at = DateTimeField(null=True, blank=True)

class AssignmentHistory.Kind:  WAIVED = "waived"
AssignmentHistory.old_waived_at / new_waived_at = DateTimeField(null=True, blank=True)
```

Then extend Slice 18's single definition **in models.py only**:

- `Ending.WAIVED = "waived"`.
- `OUTSTANDING` adds `waived_at__isnull=True`. The migration changes the partial unique constraint's condition. Check that the generated `AddConstraint`/`RemoveConstraint` pair runs on PostgreSQL.
- `_ending_expression()` and `Assignment.ending` add `waived_at → WAIVED`, after canceled and before completed. Add a `CheckConstraint` so that at most one of `completed_at`, `canceled_at` and `waived_at` is set.
- If Slice 17 has landed, its `one_live_renewal_per_completion` condition stays "not canceled". A waived renewal still occupies its completion.

Because of Slice 18, this alone does several things: the guards reject deadline changes and cancellation, manual nudges become ineligible, `assignment_ended` records `assignment_waived`, and the unsent-notification check in `_suppression_reason` suppresses with `assignment_waived`. Add tests rather than editing those functions.

### 3. Domain operation

In [assignments.py](../../training/assignments.py):

```python
def waive_assignment(*, assignment_id, actor, reason, now=None) -> tuple[Assignment, AssignmentHistory]:
```

Follow `cancel_assignment` in [assignments.py](../../training/assignments.py):

1. `_administrator_can_change(actor)`.
2. Trim the reason. It's required, up to 1000 characters: "Enter a waiver reason of at most 1000 characters."
3. Inside `atomic()`, lock the assignment.
4. Apply Decision 2A: if the assignment is the actor's own, raise `AssignmentAccessError("Another administrator must waive your own assignment.")`.
5. `_require_outstanding(assignment, "Only an active, incomplete assignment can be waived.")`.
6. Record a `WAIVED` history row with the due fields, `new_waived_at=now` and the reason. Set `waived_at` and save. Call `assignment_ended(assignment, now=now)`.

The waiver doesn't touch `completed_at`, the attempts, the slide progress, `due_*`, or archiving. Archiving a waived assignment works as it does for completed work: `canceling` is false.

### 4. Learner side

- `visible_to_learner` keeps waived assignments visible. They're not canceled.
- `learner_access` adds `LearnerDenial.WAIVED`. Viewing pages (detail, slide view, quiz result) allow it, read-only, like completed work. Mutating paths (`acknowledge_slide`, `visit_slide`'s resume update, `submit_quiz`, `take_quiz`) refuse it: `_owned_assignment` raises `AssignmentAccessError("This assignment was waived.")`. `take_quiz` and `submit_quiz_view` render `quiz_unavailable.html` with a waived message.
- **My training** puts waived assignments in a third section, **Waived**, below Completed. Each shows "Waived <date>" and no next action. A waived row never appears under "To do".

### 5. Administrator side

- **Assignment detail:** add a **Waive** form next to Cancel, shown only for outstanding assignments that aren't the actor's own. It has a required reason and a confirmation sentence that says the waiver can't be revoked (Decision 3A). Show "Waived <time> by <actor>: <reason>" in the history.
- **Report (Slice 13):** `ReportFilters.state` accepts `waived`. `in_state("waived")` uses `ended_by(WAIVED)`. The rank puts waived with the other ended work (3). The state label is "Waived", or "Archived · Waived". The deadline status is "—", as for canceled work. Scores are still shown, because they're evidence. The default `pending` view excludes waived work automatically, because pending means outstanding.
- **Export (Slice 14):** nothing to change. It follows the report, which gives "the same treatment" in lists and exports. Add a test to prove it.
- **Dashboard filter:** add a **Waived** state option.

## Tests

`training/test_waivers.py`:

- `test_waiver_requires_reason_and_administrator`.
- `test_administrator_cannot_waive_own_assignment`.
- `test_waiver_preserves_version_due_date_acknowledgments_and_attempts`.
- `test_waiver_sets_no_completion_time_or_passing_score`.
- `test_waiver_suppresses_unsent_reminders_with_reason`: a reminder that's due, then a dispatch, then `Suppressed` / `assignment_waived`.
- `test_waived_assignment_allows_a_new_assignment_of_the_training`.
- `test_waived_assignment_rejects_deadline_change_cancellation_nudge_and_quiz_submission`.
- `test_learner_sees_waived_section_and_can_review_but_not_act`.
- `test_dashboard_and_export_show_waived_consistently`: absent from pending, present in waived and in all, the same label in both.
- `test_completion_and_waiver_cannot_both_be_recorded`: one transaction wins, and the other raises the conflict message.

PostgreSQL only: `test_concurrent_waiver_and_quiz_pass_record_one_ending`.

## Out of scope

- Revoking a waiver (Decision 3B).
- Waiving many assignments at once.
- Waiving before an assignment exists, such as a standing exemption for an employee.
