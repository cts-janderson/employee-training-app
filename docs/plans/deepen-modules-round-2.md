# Deepen the training modules: round 2

## Context

The first round, [Deepen the training modules](deepen-modules.md), is done, except for a few leftovers from Slice 5 that [Slice 11](11-quiz-editing.md) picks up. A second deep-module review of `training/` found seven more places where a rule lives in several modules, or where a module's interface leaks what it should hide. Each slice below fixes one of them.

Do them **in order, one at a time**, and make sure the suite is green before starting the next slice. Slices 7 and 9 build on the slice before them. The others don't depend on each other, but the order puts the most valuable ones first.

| Slice | File | Summary | Behaviour change |
|---|---|---|---|
| 6 | [06-notification-intents.md](06-notification-intents.md) | `assignments.py` signals lifecycle intents; `notifications.py` owns the rows; one lock order, the assignment before its notifications | Yes: reminders stop as soon as an assignment ends, and an existing PostgreSQL deadlock goes away |
| 7 | [07-follow-up-summary.md](07-follow-up-summary.md) | One summary of manual nudge eligibility and the next scheduled reminder | No |
| 8 | [08-assignment-plan.md](08-assignment-plan.md) | One plan per employee, shared by assignment review and creation | No |
| 9 | [09-assignment-queries-and-labels.md](09-assignment-queries-and-labels.md) | Queryset states, one label map, one clock per request | No |
| 10 | [10-structured-draft-problems.md](10-structured-draft-problems.md) | Domain errors carry a target; the UI stops matching message text | No, though some anchors become more precise |
| 11 | [11-quiz-editing.md](11-quiz-editing.md) | Shared quiz-editing steps for attached and library quizzes, plus the Slice 5 leftovers | No |
| 12 | [12-learner-access.md](12-learner-access.md) | One learner access decision for an assignment | Only if the status-code decision says so |

## Ground rules

The [ground rules from round 1](deepen-modules.md#ground-rules) still apply, with these changes:

- Slices 6 and 12 may change behaviour, but only as their plans describe. Add tests for the new behaviour.
- If an existing test fails, stop and report it. Don't edit it, even in a slice that changes behaviour, unless the slice plan names that test.
- Use glossary terms from [GLOSSARY.md](../GLOSSARY.md) in new names. Where a slice introduces a new concept, it says so. Add the term to the glossary in the same slice.
- Respect the ADRs in [docs/adr/](../adr/). Slice 6 carries out [ADR-0004](../adr/0004-archive-assignment-without-deleting-evidence.md)'s "hidden work cannot keep sending reminders" more fully. Slice 11 keeps [ADR-0003](../adr/0003-copy-library-quiz-into-draft.md)'s separate copies.

## Test command

```bash
DATABASE_URL=sqlite:///:memory: DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py test training
```

The baseline is **110 tests, OK (skipped=1)**. After each slice, the count should rise only by the tests you added.

## Final verification

1. Run the test command. It must pass with `skipped=1`.
2. With the same environment variables, run `.venv/bin/python manage.py makemigrations --check --dry-run`. It must print "No changes detected".
3. Check that these searches find no leftover copies:

   ```bash
   grep -n "Notification" training/assignments.py                                  # no matches (Slice 6)
   grep -n "MANUAL_NUDGE_INTERVAL\|is_upcoming_reminder" training/*_views.py      # no matches (Slice 7)
   grep -n "Choose one to 100 distinct employees" training/*.py                    # assignments.py only (Slice 8)
   grep -n "PROGRESS_LABELS = \|DEADLINE_LABELS = " training/*.py                  # assignments.py only (Slice 9)
   grep -n "\.lower()" training/authoring_ui.py training/employee_views.py         # no matches (Slice 10)
   grep -n "Question.Type.TRUE_FALSE" training/quiz_authoring.py                   # no matches (Slice 11)
   ```

4. Repeat the smoke test from [round 1](deepen-modules.md#final-verification) against local PostgreSQL. Then also:
   - Cancel an assignment with scheduled reminders.
   - Open its notification records. The reminders must show **Suppressed**, not **Queued for delivery**.
5. Run the PostgreSQL-only concurrency tests from Slice 6 against local PostgreSQL. SQLite skips them.
6. For each slice, report the test count and any departure from these plans, with the reason.

## Review status

These plans were checked against the code by hand, then reviewed by Codex (gpt-6-sol) on 2026-10-27. There were two independent runs:

1. **A review of the written plans.** It led to these changes:
   - Slice 6 found a deadlock that exists today, between `invalidate_prior_reminders` and `retry_notification`.
   - Slice 8: creation must decide per employee at execution time, not from a batch snapshot.
   - Slice 10: two places that turned errors into strings lost the target.
   - Slice 12: `MISSING` must keep raising `DoesNotExist`.
   - Some helpers were dropped because they fail the deletion test.
2. **An independent design for three problems, written without seeing these plans:** ending notifications, the follow-up summary, and the editor pre-checks.
   - It agreed on which notification kinds to suppress, and on keeping `ASSIGNED` and `DEADLINE_CHANGED` with the worker.
   - It proposed the assignment-first lock order. That lock order let Slice 6 also suppress `BLOCKED` and `EXHAUSTED` follow-ups.
   - It suggested reusing the detail page's notification list in Slice 7, and calling `assignment_ended` only when the assignment actually ends.
   - **Not adopted:** it proposed deleting the view pre-check on the editors' save path. That would let input errors (400) win over a stale-draft conflict (409), which is a behaviour change. See [Slice 11](11-quiz-editing.md#4-remove-the-dead-code).

## Known defects these slices don't cover

The review also found these. They are out of scope here. Fix them in a separate change, or add them to a slice if you decide they belong there.

- `update_demo_employee` in [employees.py](../../training/employees.py) takes an `actor` but never calls `is_administrator`. Only the view decorator guards it.
- `archive_assignment_view` doesn't catch `AssignmentAccessError`, and `restore_assignment_view` catches nothing ([assignment_admin_views.py](../../training/assignment_admin_views.py)). Both can return 500s.
- `publish_saved_draft` ([services.py](../../training/services.py)) and `save_message` ([authoring_ui.py](../../training/authoring_ui.py)) call `timezone.now()` directly. They have no `now` parameter.
- The `auto_now_add` fields `Assignment.assigned_at` and `EmployeeProfileChange.changed_at` ignore an injected clock. Fixing that needs a migration.
- No domain test calls `_complete_if_satisfied` or `restore_assignment` directly.
