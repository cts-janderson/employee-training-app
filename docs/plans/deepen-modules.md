# Deepen the training modules

## Context

A deep-module review of `training/` found rules that each live in three to six places, mostly re-derived in views. The first fix, notification state transitions, is done. [notifications.py](../../training/notifications.py) now owns `next_state`, `delivery_result`, `_save_transition`, `manual_nudge_problem` and `is_upcoming_reminder`. Their tests are in `NotificationTransitionTests` in [test_notifications.py](../../training/test_notifications.py). Use those as the example to follow.

The remaining fixes are split into five slices, each in its own file. Do them **in order, one at a time**, and make sure the suite is green before starting the next slice.

| Slice | File | Summary |
|---|---|---|
| 1 | [01-administrator-check.md](01-administrator-check.md) | One administrator check; the only behaviour change |
| 2 | [02-cancellation-history.md](02-cancellation-history.md) | Shared cancellation-history helper |
| 3 | [03-draft-concurrency-guard.md](03-draft-concurrency-guard.md) | Shared lock-and-check for training version drafts |
| 4 | [04-assignment-lifecycle.md](04-assignment-lifecycle.md) | One place each for the outstanding filter, state, slide completion, completion and next step |
| 5 | [05-question-types.md](05-question-types.md) | One module per question type for authoring, validation and grading |

## Ground rules

- **Refactor, not redesign.** Apart from Slice 1, observable behaviour must not change: HTTP status codes, redirects, template context keys and **every user-facing message, character for character**. Tests assert messages with `assertContains` and `assertRaisesMessage`.
- **Do not edit existing tests to make them pass.** You may add tests and move fixtures into mixins, as `NotificationFixtures` does in [test_notifications.py](../../training/test_notifications.py). If an existing test fails, the refactor is wrong.
- **Follow the surrounding style.**
  - Plain module-level functions with keyword-only `*, now=None` clock parameters.
  - Compact lines up to about 120 characters.
  - Docstrings only where a rule isn't obvious.
  - No new dependencies. No formatter or linter is configured.
- **Use glossary terms** from [GLOSSARY.md](../GLOSSARY.md) in new names.
- **No migrations.** No changes to model fields or constraints. A custom queryset manager (Slice 4) adds no migration.
- **Don't widen scope.** Each slice file has an "Out of scope" list. Leave those areas alone even if they look related.

## Test command

```bash
DATABASE_URL=sqlite:///:memory: DJANGO_DEBUG=1 DEV_AUTH_STUB=1 .venv/bin/python manage.py test training
```

The baseline is **101 tests, OK (skipped=1)**. The skipped test is the PostgreSQL row-lock test in [test_manual_nudges.py](../../training/test_manual_nudges.py). After each slice, the count should rise only by the tests you added.

## Final verification

1. Run the test command. It must pass with `skipped=1`.
2. With the same environment variables, run `.venv/bin/python manage.py makemigrations --check --dry-run`. It must print "No changes detected".
3. Check that these searches find no leftover copies:

   ```bash
   grep -rn "completed_at__isnull=True, canceled_at__isnull=True" training/*.py   # only models.py (constraint)
   grep -rn "Role.ADMINISTRATOR" training/assignments.py training/notifications.py  # no matches
   grep -rn "Question.Type.ORDERING" training/quizzes.py training/quiz_learner_views.py  # no matches
   ```

4. Smoke-test against local PostgreSQL configured as in [README.md](../../README.md):
   - Run `.venv/bin/python manage.py runserver` with `DJANGO_DEBUG=1 DEV_AUTH_STUB=1`.
   - As the demo administrator, create a training with one question of each type, publish it and assign it to a demo employee.
   - Switch to the employee. Acknowledge the slides, fail the quiz once, then pass it.
   - Confirm the dashboard's state, progress, deadline and next-action labels are unchanged.
   - Run `.venv/bin/python manage.py dispatch_notifications` and check **Notifications**.
5. For each slice, report the test count and any departure from these plans, with the reason.

## Review status

This plan was checked against the code by hand. Two claims were verified:
- Every test fixture and `bootstrap_demo_admin` create administrators with `is_demo=True`.
- No test uses `assertNumQueries`.

An independent Codex review was attempted but couldn't run from this environment.
