# Slice 25: ending an assignment

Part of [Deepen the training modules: round 3](deepen-modules-round-3.md). It builds on [Slice 2](02-cancellation-history.md), [Slice 18](18-assignment-endings.md) and [Slice 19](19-waivers.md). **Make the decision below before you start.**

## Problem

An administrator ends an assignment by **cancellation** or **waiver**. **Archiving** unfinished work also cancels it, in the same transaction ([ADR-0004](../adr/0004-archive-assignment-without-deleting-evidence.md)). Each of these writes the same steps by hand in [assignments.py](../../training/assignments.py):

| Step | `cancel_assignment` (418–430) | `waive_assignment` (433–455) | `archive_assignment` (458–479) |
|---|---|---|---|
| Administrator check | `_administrator_can_change(actor)` (419) | the same (434) | the same (459) |
| Reason check | `if not reason or len(reason) > 1000:` then `"Enter a cancellation reason of at most 1000 characters."` (421–422) | the same, with `"Enter a waiver reason of at most 1000 characters."` (436–437) | the same, with `"Enter an archive reason of at most 1000 characters."` (461–462) |
| Row lock | `Assignment.objects.select_for_update().get(pk=assignment_id, employee__is_demo=True)` (425) | the same (440) | the same (465) |
| Outstanding guard | `_require_outstanding(assignment, "Only an active, incomplete assignment can be canceled.")` (426) | `"... can be waived."` (446), after the self-waiver check (441–442) | `canceling = assignment.is_outstanding` (468) |
| History row | `_record_cancellation(...)` (427) | its own `AssignmentHistory.objects.create(...)` (447–451) | `_record_cancellation(...)` (470) |
| Timestamp | `assignment.canceled_at = now`, inside `_record_cancellation` (414) | `assignment.waived_at = now` (452) | inside `_record_cancellation` |
| Save | `save(update_fields=["canceled_at"])` (428) | `save(update_fields=["waived_at"])` (453) | `save(update_fields=["canceled_at", "archived_at"])` (476) |
| Signal | `assignment_ended(assignment, now=now)` (429) | the same (454) | the same, `if canceling:` (477–478) |

The history rows are copies. `_record_cancellation` (405–415) writes:

```python
old_due_date=assignment.due_date, new_due_date=assignment.due_date,
old_due_timezone=assignment.due_timezone, new_due_timezone=assignment.due_timezone,
old_due_at=assignment.due_at, new_due_at=assignment.due_at,
old_due_revision=assignment.due_revision, new_due_revision=assignment.due_revision,
old_canceled_at=None, new_canceled_at=now, reason=reason,
```

`waive_assignment` repeats the first four lines field for field (448–450). Only the kind and `new_waived_at=now` differ. `old_waived_at` is left at its `None` default, so the rows have the same shape.

Completion is the third ending, but it doesn't follow these steps. It has no actor, no reason and no history row. `_complete_if_satisfied` (545–553) sets `completed_at`. Its two callers save and signal by hand:
- `acknowledge_slide`: `save(update_fields=["started_at", "resume_slide", "completed_at"])`, then `if completed: assignment_ended(...)` ([assignments.py](../../training/assignments.py) 572–574);
- `submit_quiz`: `save(update_fields=["completed_at"])`, then `assignment_ended(...)` ([quizzes.py](../../training/quizzes.py) 132–134).

The admin views in [assignment_admin_views.py](../../training/assignment_admin_views.py) each repeat `get_object_or_404`, a `try`, and their own mapping from errors to responses. The mappings differ:

| View | `AssignmentInputError` | `AssignmentConflict` | `AssignmentAccessError` |
|---|---|---|---|
| `change_deadline_view` (203–217) | 400 detail page | 409 detail page | **400** `HttpResponseBadRequest("Administrator access is required.")` (214–215) |
| `cancel_assignment_view` (221–235) | 400 detail page | 409 detail page | **400** `HttpResponseBadRequest("Administrator access is required.")` (232–233) |
| `waive_assignment_view` (239–250) | 400 detail page | 409 detail page | **403** detail page with the message (248–249) |
| `archive_assignment_view` (255–264) | 400 detail page | not caught | **not caught: 500** |
| `restore_assignment_view` (269–272) | nothing caught | nothing caught | **not caught: 500** |

The two 500s are the [round 2 known defect](deepen-modules-round-2.md#known-defects-these-slices-dont-cover). They are still there.

None of the 400 or 500 responses for `AssignmentAccessError` can be reached over HTTP today. `demo_admin_required` and `demo_actor_required` ([dev_auth.py](../../training/dev_auth.py)) already reject an actor who isn't an administrator, isn't active or isn't a demo employee. Those are exactly the conditions `is_administrator` checks in [employees.py](../../training/employees.py), on the same `request.employee`. The decorators return 403 or 503 first. The only `AssignmentAccessError` that reaches a view is the self-waiver one, which already gets 403. So the mappings disagree only on paths that are unreachable today. They would start to matter if someone called these views without the decorators, or added a new reason for denial.

## Decision needed

**Decision 1: the status for `AssignmentAccessError` in admin views.**

- **A. Keep each view's mapping.** The shared helper takes a per-view mapping. It's a pure refactor, but it keeps the inconsistency.
- **B. 403 everywhere, on the detail page with the error message (recommended).** `str(exc)` is already `"Administrator access is required."`, so the text is unchanged. It's a refusal, not a bad request, so 403 is the right status. `waive_assignment_view` already does this.
- **C. 403 everywhere, as a plain `HttpResponseForbidden(str(exc))`.** This matches the decorator's plain 403. But the self-waiver 403 would lose its detail page. That changes a reachable response.

**Decision 2: the 500s in `archive_assignment_view` and `restore_assignment_view`.**

- **A. Leave them.** They can't be reached through the decorators.
- **B. Route both views through the shared mapping (recommended).** Archive and restore then answer `AssignmentAccessError` with 403, like the other views. Archive also gains 409 for `AssignmentConflict`, which it can't raise today.

Under 1B and 2B, **no existing test assertion changes**. No test reaches a view's `AssignmentAccessError` branch. The HTTP 403s in [test_assignment_admin.py](../../training/test_assignment_admin.py) (266–273) and [test_assignment_archive.py](../../training/test_assignment_archive.py) (103–104) come from `demo_admin_required`. The only domain assertion is `assertRaisesMessage(AssignmentAccessError, "Administrator access is required.")` ([test_assignments.py](../../training/test_assignments.py) 76), and the domain is unchanged. These are the behaviour changes, all on paths the decorators currently block:

| View | Today | After 1B and 2B |
|---|---|---|
| `change_deadline_view`, `AssignmentAccessError` | 400, plain text | 403, detail page, same text |
| `cancel_assignment_view`, `AssignmentAccessError` | 400, plain text | 403, detail page, same text |
| `archive_assignment_view`, `AssignmentAccessError` | 500 | 403, detail page |
| `restore_assignment_view`, `AssignmentAccessError` | 500 | 403, detail page |
| `restore_assignment_view`, `AssignmentInputError` or `AssignmentConflict` | 500 | 400 or 409, detail page (not raised today) |

**Decision 3: whether completion goes through the shared ending.**

- **A. Leave completion out (recommended).** Completion has no actor, no reason, no history row and no administrator check. It gets its guard from `learner_access`. `acknowledge_slide` also saves `started_at` and `resume_slide` in the same `save`. The only step it shares is "set the timestamp, save, call `assignment_ended`". Apply the deletion test to a helper for that step. Deleting it would put two lines back in each of two callers, and the helper would need an `extra_fields` argument for `acknowledge_slide`. It doesn't earn its interface.
- **B. Share only the "timestamp, save, signal" step.** It adds a parameter for the extra save fields and saves one line per caller.

**Decision: 1B, 2B and 3A**, recorded 2026-10-27. `AssignmentAccessError` gives a 403 detail page in every admin view; archive and restore use the shared mapping; completion stays out of the shared ending.

## Change

### 1. One reason check

In [assignments.py](../../training/assignments.py):

```python
def _clean_reason(reason, *, message):
    """Return the stripped reason, or raise AssignmentInputError(message) if it's empty or over 1000 characters."""
```

- `end_assignment` (step 3) and `archive_assignment` call it with today's three messages, character for character. The messages differ in their article ("a" and "an"), so each caller passes the whole message, not a noun.
- The 1000-character limit lives only here in [assignments.py](../../training/assignments.py). `reconcile_notification` in [notifications.py](../../training/notifications.py) has its own check (449) and is out of scope.

### 2. One recorded ending

Replace `_record_cancellation` with:

```python
_ENDINGS = {
    Ending.CANCELED: _EndingRule(timestamp="canceled_at", kind=AssignmentHistory.Kind.CANCELED,
                                 conflict="Only an active, incomplete assignment can be canceled."),
    Ending.WAIVED: _EndingRule(timestamp="waived_at", kind=AssignmentHistory.Kind.WAIVED,
                               conflict="Only an active, incomplete assignment can be waived."),
}

def _record_ending(assignment, *, ending, actor, reason, now) -> AssignmentHistory:
    """Write the history row, set the timestamp, save it and call assignment_ended."""
```

- `_EndingRule` is a small frozen dataclass or a named tuple. Follow the surrounding style. It also holds `reason_message`: "Enter a cancellation reason of at most 1000 characters." or "Enter a waiver reason of at most 1000 characters.".
- The history row keeps today's fields: `occurred_at`, the four due-field pairs with `old_* == new_*`, `new_<timestamp>=now` and the reason. Build the due pairs once, in `_record_ending`. It's the only caller, so don't add a separate `_snapshot_fields` helper.
- It saves with `update_fields=[rule.timestamp]`, then calls `assignment_ended(assignment, now=now)`.
- `Ending.COMPLETED` has no entry. Passing it raises `KeyError`. That's a programming error, not an input error.

### 3. One operation for administrator endings

```python
def end_assignment(*, assignment_id, ending, actor, reason, now=None) -> tuple[Assignment, AssignmentHistory]:
```

The arguments are keyword-only, like every other operation in [assignments.py](../../training/assignments.py). It runs today's steps in today's order:

1. `_administrator_can_change(actor)`.
2. `reason = _clean_reason(reason, message=rule.reason_message)`.
3. `now = now or timezone.now()`.
4. Inside `transaction.atomic()`, lock the row with `select_for_update().get(pk=assignment_id, employee__is_demo=True)`.
5. For `Ending.WAIVED` only: if `assignment.employee_id == actor.id`, raise `AssignmentAccessError("Another administrator must waive your own assignment.")`. This rule comes from [ADR-0006](../adr/0006-final-waivers-by-any-other-administrator.md). It stays **before** the outstanding guard, as today, so a self-waiver of ended work still raises the access error. Keep it as a visible `if` in `end_assignment`, not as a callback in the rule.
6. `_require_outstanding(assignment, rule.conflict)`.
7. `history = _record_ending(...)`, then return `(assignment, history)`.

`cancel_assignment` and `waive_assignment` keep their signatures and return values. Each becomes a one-line call to `end_assignment`. Tests and views import them by name, and they use the glossary's terms.

### 4. Archiving uses the recorded ending

`archive_assignment` keeps its own lock, its `archived_at` early return and its `ARCHIVED` history row. It no longer writes the cancellation itself:

```python
if assignment.is_outstanding:
    _record_ending(assignment, ending=Ending.CANCELED, actor=actor, reason=reason, now=now)
AssignmentHistory.objects.create(...)  # ARCHIVED, unchanged
assignment.archived_at = now
assignment.save(update_fields=["archived_at"])
```

- The cancellation and the archive stay in one transaction, as ADR-0004 requires.
- The `CANCELED` row is still created before the `ARCHIVED` row.
- `assignment_ended` now runs before `archived_at` is saved instead of after. Its result doesn't depend on it, because it reads only `assignment.ending`, which is `canceled` either way.
- The `canceling` variable goes away.

### 5. One error mapping in admin views

In [assignment_admin_views.py](../../training/assignment_admin_views.py):

```python
_ACTION_ERROR_STATUS = ((AssignmentInputError, 400), (AssignmentConflict, 409), (AssignmentAccessError, 403))

def _run_assignment_action(request, assignment_id, action):
    """404 for a missing assignment, the detail page with the error's status, or a redirect to the detail page."""
```

- `change_deadline_view`, `cancel_assignment_view`, `waive_assignment_view`, `archive_assignment_view` and `restore_assignment_view` each become one call, with a `lambda` that calls the domain function with today's arguments.
- The error page is `_render_detail(request, assignment_id, error=str(exc), status=status)`, as today.
- Remove the `HttpResponseBadRequest` import if nothing else uses it. `assignment_dashboard` uses it at line 136, so it probably stays.
- `send_manual_nudge_view` stays as it is. It maps different errors (429 and 409) and redirects to the notification.

### 6. Completion

No change (Decision 3A). `_complete_if_satisfied`, `acknowledge_slide` and `submit_quiz` stay as they are.

## Out of scope

- Completion's save-and-signal steps (Decision 3A).
- `restore_assignment`'s domain code. Only its view changes.
- `change_assignment_deadline`'s domain code. It isn't an ending. Only its view shares the error mapping.
- The `get_object_or_404` pre-check before each action. Keep it, so a missing assignment stays 404.
- Learner views and `learner_access` ([Slice 12](12-learner-access.md)).
- Revoking a waiver, and waiving or canceling many assignments at once.
- A direct domain test for `_complete_if_satisfied`. That gap from round 2 is still open.

## Tests

Add these to [test_assignment_endings.py](../../training/test_assignment_endings.py):

- `test_each_administrator_ending_records_the_same_history_shape`. Use `subTest` for cancellation, waiver, and archiving of outstanding work. For each one, check:
  - the kind;
  - that `actor`, `occurred_at` and the trimmed `reason` are set;
  - that each `old_due_*` equals its `new_*`, and both equal the assignment's due fields;
  - that only the matching `new_<timestamp>` is set, and every other `old_`/`new_` ending and archive field is `None`, except `new_archived_at` for archiving;
  - that a due reminder is `Suppressed` with `assignment_canceled` or `assignment_waived`.
- `test_each_reason_check_keeps_its_message`. Use `subTest` for the three operations, with `" "` and with 1001 characters. Use `assertRaisesMessage` with today's exact messages. Check that no history row is written.
- `test_self_waiver_of_ended_work_raises_access_error_before_conflict`. This pins the order of step 3.5.
- `test_end_assignment_rejects_completion`: `Ending.COMPLETED` raises `KeyError`, and nothing is written.

Add `RestoreAssignmentTests` to [test_assignment_archive.py](../../training/test_assignment_archive.py). They call `restore_assignment` directly:

- `test_restore_clears_archived_at_and_records_restoration`: a `RESTORED` row with `old_archived_at` set to the archive time and `new_archived_at=None`.
- `test_restore_of_unarchived_assignment_changes_nothing`: the same assignment comes back, and no history row is written.
- `test_restore_does_not_reopen_a_canceled_obligation`: archive pending work, then restore it. `canceled_at` stays set, `ending` stays `canceled`, and the work is still absent from `visible_to_learner` (ADR-0004 and the glossary's **Restoration**).
- `test_restore_requires_administrator`: `AssignmentAccessError("Administrator access is required.")`.

Add one table-driven HTTP test to [test_assignment_admin.py](../../training/test_assignment_admin.py), `test_admin_assignment_actions_share_one_error_mapping`:

- For each of the five views and each of the three errors, patch the domain function in `training.assignment_admin_views` with `unittest.mock.patch(..., side_effect=...)`. Check the status and that the message appears on the detail page.
- Keep one unpatched case per reachable response: a blank reason gives 400, ended work gives 409, and a self-waiver gives 403 with "Another administrator must waive your own assignment.".
- This test pins the Decision 1B and 2B rows above.

All existing tests must pass unchanged. The count rises from 187 by the tests you add, with `skipped=3`.

## Done when

- The test command from [round 2](deepen-modules-round-2.md#test-command) passes.
- `.venv/bin/python manage.py makemigrations --check --dry-run` prints "No changes detected".
- These searches match as noted:

  ```bash
  grep -n "len(reason) > 1000" training/assignments.py                          # one match: _clean_reason
  grep -n "new_due_revision=assignment.due_revision," training/assignments.py   # one match: _record_ending
  grep -n "_record_cancellation" training/*.py                                  # no matches
  grep -n "assignment_ended(assignment" training/assignments.py                 # two matches: _record_ending, acknowledge_slide
  grep -n "select_for_update().get(pk=assignment_id" training/assignments.py    # four matches: deadline, end_assignment, archive, restore
  grep -n "Administrator access is required" training/assignment_admin_views.py # no matches
  grep -n "except AssignmentAccessError" training/assignment_admin_views.py     # one match: _run_assignment_action
  grep -n "at most 1000 characters" training/assignments.py                     # three matches, the messages unchanged
  ```
