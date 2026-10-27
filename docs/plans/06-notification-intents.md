# Slice 6: notification intents

Part of [Deepen the training modules: round 2](deepen-modules-round-2.md). This slice changes behaviour.

## Problem

[assignments.py](../../training/assignments.py) knows how notifications are stored. The notifications seam leaks in both directions:

| Leak | Where |
|---|---|
| Builds `ASSIGNED` rows field by field | `create_assignments`, inside the `Assignment.objects.create` block |
| Builds `DEADLINE_CHANGED` rows field by field | `change_assignment_deadline` |
| Lazily imports `schedule_reminders` and `invalidate_prior_reminders`, though there's no import cycle to avoid | both functions above |
| Both rows skip `status_changed_at`, so they get the model default `timezone.now` instead of the injected `now` | both functions above |

The leak is also uneven:
- A deadline change suppresses the old reminders straight away, in `invalidate_prior_reminders`.
- Cancellation, archiving and completion leave scheduled reminders `PENDING`. They are only suppressed when the worker claims them at their scheduled time, in `_suppression_reason`. Until then, the notification log shows them as **Queued for delivery**.
- Meanwhile, [assignment_admin_detail.html](../../templates/training/assignment_admin_detail.html) promises that cancel-and-archive "stops future reminders".

There is also a deadlock risk on PostgreSQL that exists today:
- `retry_notification` and `reconcile_notification` lock the notification, then the assignment. `dispatch_claimed` does the same.
- `change_assignment_deadline` locks the assignment, then `invalidate_prior_reminders` updates `BLOCKED` and `EXHAUSTED` reminder rows.
- A deadline change that runs at the same time as an admin retry of a blocked reminder can therefore deadlock. Ending an assignment immediately would add another path that locks the assignment and then notification rows.

## Change

### 1. One lock order: the assignment before its notifications

In [notifications.py](../../training/notifications.py), change `dispatch_claimed`, `retry_notification` and `reconcile_notification` so they lock the assignment first:

1. Read `assignment_id` without a lock, with `Notification.objects.values_list("assignment_id", flat=True).get(pk=notification_id)`. A notification never changes assignment.
2. Lock the assignment with `select_for_update()`, keeping each function's current `select_related`.
3. Lock the notification with `select_for_update()`, then run the existing status or claim-token checks **after** both locks are held. Keep their messages unchanged.

`claim_next_notification` keeps its short lock on the notification alone, because it never touches the assignment. `request_manual_nudge` already locks the assignment first.

Add a comment above `dispatch_claimed` stating the rule: lock the assignment before any of its notifications.

### 2. Lifecycle intents in `notifications.py`

Add three public functions to [notifications.py](../../training/notifications.py), next to `schedule_reminders`. Each one runs inside the caller's transaction, after the caller has locked and saved the assignment.

| Function | Does |
|---|---|
| `assignment_created(assignment, *, now)` | Creates the `ASSIGNED` notification, then calls `schedule_reminders(assignment, now=now)`. Returns the `ASSIGNED` notification. |
| `deadline_changed(assignment, *, old_revision, now)` | Creates the `DEADLINE_CHANGED` notification, then calls `invalidate_prior_reminders(assignment, old_revision=old_revision, now=now)` and `schedule_reminders(assignment, now=now)`. Returns the `DEADLINE_CHANGED` notification. |
| `assignment_ended(assignment, *, now)` | Suppresses the assignment's undelivered follow-ups (see below). Returns the number of rows updated. |

Both rows that get created must keep today's field values, `due_revision=assignment.due_revision` and `next_attempt_at=now`, and also set `status_changed_at=now`.

`assignment_ended` runs one `UPDATE`:

```python
Notification.objects.filter(
    assignment=assignment,
    kind__in=[*REMINDER_KINDS, Notification.Kind.MANUAL_NUDGE],
    status__in=[Notification.Status.PENDING, Notification.Status.RETRY,
                Notification.Status.BLOCKED, Notification.Status.EXHAUSTED],
).update(status=Notification.Status.SUPPRESSED, suppression_reason=reason, status_changed_at=now)
```

Here `reason` is `"assignment_completed"` if `assignment.completed_at` is set, and `"assignment_canceled"` otherwise. These are the same reasons `_suppression_reason` uses, and in the same order of precedence.

Why these kinds and statuses:
- **Kinds.** Scheduled reminders and manual nudges are follow-ups about an obligation, and the admin page promises that they stop. `ASSIGNED` and `DEADLINE_CHANGED` report events that have already happened. They keep going through the worker, which still suppresses them. `test_completed_canceled_and_inactive_work_is_suppressed_before_sending` in [test_notifications.py](../../training/test_notifications.py) pins that path. It expects `process_ready_notifications` to return the suppressed `ASSIGNED` rows.
- **Manual nudges stay rows.** They are suppressed, not deleted, so `next_manual_nudge_time` still sees the latest request.
- **Statuses.** `PENDING`, `RETRY`, `BLOCKED` and `EXHAUSTED` could still be delivered or retried. Leave the others alone:
  - `SENT` and `SUPPRESSED` are final.
  - `SENDING` belongs to a worker claim. With step 1, `dispatch_claimed` waits on the assignment lock and then suppresses the row itself.
  - `AMBIGUOUS` must stay open to reconciliation, because the email may already have gone out.

Keep `_suppression_reason` unchanged. It remains the backstop.

### 3. Call the intents from `assignments.py`

In [assignments.py](../../training/assignments.py):
- Import `assignment_created`, `assignment_ended` and `deadline_changed` at module level from `.notifications`. [notifications.py](../../training/notifications.py) doesn't import `.assignments`, so there is no cycle.
- `create_assignments`: replace the `Notification.objects.create(...)` call and the lazy `schedule_reminders` import and call with `assignment_created(assignment, now=now)`.
- `change_assignment_deadline`: replace the `Notification.objects.create(...)` call and the lazy imports with `deadline_changed(assignment, old_revision=old_revision, now=now)`.
- `cancel_assignment`: call `assignment_ended(assignment, now=now)` after `assignment.save(...)`.
- `archive_assignment`: call `assignment_ended(assignment, now=now)` only when this archive also cancels unfinished work. That is the branch that calls `_record_cancellation`. Make the call after the save. Archiving finished work, archiving twice and `restore_assignment` don't call it.
- `acknowledge_slide`: when `_complete_if_satisfied` returns true, call `assignment_ended(assignment, now=now)` after the save.
- Remove `Notification` from the `.models` import.

In [quizzes.py](../../training/quizzes.py), in `submit_quiz`, call `assignment_ended(assignment, now=now)` after saving `completed_at`. Do this only when `_complete_if_satisfied` returned true.

### 4. Glossary

The glossary already defines **Scheduled reminder** and **Manual nudge**. Don't add a term for "follow-up". It is used only in this plan and in code comments.

## Out of scope

- Employee deactivation. Reminders for inactive employees are already suppressed by `_suppression_reason` and `_inactive_during_reminder_window`, and reactivation must be able to resume them.
- Changing what the notification log page says about suppressed rows.
- `request_manual_nudge`. It already sets `status_changed_at=now` and locks the assignment first.

## Tests

Add `NotificationIntentTests(NotificationFixtures, TestCase)` to [test_notifications.py](../../training/test_notifications.py). Use the fixture clock, and call domain functions directly.

- `test_canceling_assignment_suppresses_pending_reminders`: both reminders become `SUPPRESSED`, with reason `"assignment_canceled"` and `status_changed_at` equal to the cancel time. The `ASSIGNED` row's status is unchanged.
- `test_archiving_pending_assignment_suppresses_pending_reminders`: same as above, through `archive_assignment`.
- `test_archiving_completed_assignment_leaves_notifications_alone`.
- `test_completing_by_acknowledgment_suppresses_pending_reminders`: no quiz, acknowledge the last slide, reason `"assignment_completed"`.
- `test_passing_quiz_suppresses_pending_reminders`: reason `"assignment_completed"`.
- `test_failing_quiz_leaves_reminders_pending`.
- `test_ending_assignment_suppresses_blocked_and_exhausted_reminders`.
- `test_ending_assignment_leaves_sent_claimed_and_ambiguous_notifications_alone`: set one reminder to each of `SENT`, `SENDING` and `AMBIGUOUS` before canceling.
- `test_queued_manual_nudge_is_suppressed_and_still_rate_limits`: after the cancel, `next_manual_nudge_time` still returns the request time plus 24 hours.
- `test_created_and_deadline_changed_notifications_use_injected_clock`: `status_changed_at == now` for both kinds.
- `test_retry_and_reconcile_still_check_status_after_locking`: retrying a `SENT` notification still raises "Only blocked or exhausted notifications can be retried.", and reconciling a non-ambiguous one still raises its message.

Add PostgreSQL-only concurrency tests in a `TransactionTestCase`. Skip them on SQLite, the way `ConcurrentManualNudgeTests` in [test_manual_nudges.py](../../training/test_manual_nudges.py) is skipped.
- A deadline change and `retry_notification` on a blocked reminder of the same assignment, run in two threads. Neither may deadlock, and the result is consistent.
- A cancellation that runs while `dispatch_claimed` holds a claimed reminder. The reminder ends `SUPPRESSED` with reason `"assignment_canceled"` and no delivery attempt, or `SENT` if dispatch got the assignment lock first. It must never be left `SENDING`.

Add one view test to [test_assignment_admin.py](../../training/test_assignment_admin.py): after canceling, the notification detail page for a reminder contains "Suppressed" and not "Queued for delivery".

These existing tests must still pass unchanged. They exercise the worker backstop:
- `test_completed_canceled_and_inactive_work_is_suppressed_before_sending` and `test_retry_after_cancellation_is_suppressed` in [test_notifications.py](../../training/test_notifications.py);
- `test_completion_cancellation_and_retry_recheck_eligibility` in [test_reminders.py](../../training/test_reminders.py);
- `test_cancellation_suppresses_queued_nudge` and `test_completion_suppresses_queued_nudge_and_rejects_new_request` in [test_manual_nudges.py](../../training/test_manual_nudges.py).

## Done when

- The test command passes.
- The PostgreSQL-only tests pass against local PostgreSQL.
- `grep -n "Notification" training/assignments.py` finds no matches.
- `grep -n "from .notifications import" training/assignments.py` shows a single module-level import.
