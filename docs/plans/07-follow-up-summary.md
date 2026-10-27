# Slice 7: follow-up summary

Part of [Deepen the training modules: round 2](deepen-modules-round-2.md). Do [Slice 6](06-notification-intents.md) first.

## Problem

Two admin views each work out the same three facts about an assignment's follow-ups, in different ways:

| Fact | `assignment_dashboard` | `_detail_context` |
|---|---|---|
| When the next manual nudge is allowed | Imports `MANUAL_NUDGE_INTERVAL` and recomputes `latest + interval > now` from a batch query | Calls `next_manual_nudge_time` (one query) |
| Whether a manual nudge can be sent | `not manual_nudge_problem(...) and not row["manual_next_at"]` | the same expression, written again |
| The next scheduled reminder | Takes the minimum `scheduled_at` over `is_upcoming_reminder` rows | Filters `is_upcoming_reminder` rows in `scheduled_at` order. The template then takes `upcoming_reminders.0`. |

Both views are in [assignment_admin_views.py](../../training/assignment_admin_views.py), and the rules come from [notifications.py](../../training/notifications.py). The view imports the rate-limit constant, so the interval rule leaks across the seam. The only tests of the rate-limit display go through the dashboard.

## Change

### 1. One summary in `notifications.py`

Add this to [notifications.py](../../training/notifications.py):

```python
@dataclass(frozen=True)
class FollowUpSummary:
    manual_next_at: datetime | None       # set only while the rolling limit applies
    manual_can_send: bool
    upcoming_reminders: list              # current-revision reminders still waiting, in scheduled_at order
    notifications: list                   # every notification for the assignment, in scheduled_at, created_at order

    @property
    def next_scheduled_reminder(self):
        return self.upcoming_reminders[0] if self.upcoming_reminders else None


def follow_up_summaries(assignments, *, now):
    """Return {assignment.id: FollowUpSummary} using one notification query."""
```

Notes:
- `assignments` must have `employee` loaded. Both callers already use `select_related("employee", ...)`.
- `follow_up_summaries` runs one query: `Notification.objects.filter(assignment_id__in=...).order_by("scheduled_at", "created_at")`.
  - For manual nudges it keeps the latest `requested_at`, whatever the nudge's status. A suppressed or failed nudge still counts toward the rolling limit, as in `next_manual_nudge_time`.
  - For everything else it keeps the rows where `is_upcoming_reminder(item, assignment, now)` is true.
  - The `scheduled_at, created_at` order is the detail page's current order. The dashboard's tie-break between two reminders with the same `scheduled_at` may change from `requested_at` to `created_at`. Reminders never have `requested_at`, so the visible result is the same.
- Extract the rolling-limit arithmetic into a private `_manual_next_at(latest_requested_at, now)` that returns `None` or the next eligible time. Call it from both `next_manual_nudge_time` and `follow_up_summaries`. `request_manual_nudge` keeps calling `next_manual_nudge_time` under its row lock.
- `manual_can_send = not manual_nudge_problem(assignment) and manual_next_at is None`.

### 2. Views become adapters

In [assignment_admin_views.py](../../training/assignment_admin_views.py):
- **`assignment_dashboard`:** replace the `if rows:` block with one `follow_up_summaries([...], now=now)` call. Copy `manual_next_at`, `manual_can_send` and `next_scheduled_reminder` into each row under the **same keys** the template uses today.
- **`_detail_context`:** take the summary for the one assignment. Fill `notifications`, `manual_next_at`, `manual_can_send` and `upcoming_reminders` from it, and delete the separate `assignment.notifications.order_by(...)` query, so the page doesn't gain a query. Keep both the `notifications` and `upcoming_reminders` keys. `upcoming_reminders` must stay a list, because `test_reminders.py` asserts its length and the template loops over it at line 96.
- Remove `MANUAL_NUDGE_INTERVAL`, `is_upcoming_reminder` and `next_manual_nudge_time` from the view's imports. Keep `manual_nudge_problem` only if something else in the view still uses it.

[assignment_dashboard.html](../../templates/training/assignment_dashboard.html) and [assignment_admin_detail.html](../../templates/training/assignment_admin_detail.html) need no changes.

## Out of scope

- An `actionable()` queryset method or `is_actionable` predicate. The review counted the actionable-assignment rule in four places, but each copy produces a different message or suppression reason, so a shared predicate would remove little. Reconsider it if a fifth copy appears.
- The notification log's own list view.

## Tests

Add `FollowUpSummaryTests(NotificationFixtures, TestCase)` to [test_notifications.py](../../training/test_notifications.py). Use a fixed `now`.

- With no manual nudge, `manual_next_at` is `None` and `manual_can_send` is true.
- With a nudge requested 23 hours ago, `manual_next_at` is the request time plus 24 hours and `manual_can_send` is false.
- With a nudge requested exactly 24 hours ago, the limit has passed and sending is allowed.
- For a completed, canceled or inactive-employee assignment, `manual_can_send` is false and `upcoming_reminders` is empty.
- `next_scheduled_reminder` is the earliest current-revision reminder. A superseded revision's reminder never appears, and neither does one whose time has passed.
- With the latest nudge `SUPPRESSED` and requested 23 hours ago, the rolling limit still applies.
- Several assignments in one call give independent summaries. Assert the call uses a single notification query with `assertNumQueries` on a prepared list of assignments.
- The admin detail page runs no more queries than it did before this slice. Measure the count on the baseline first, then pin it with `assertNumQueries`.

## Done when

- The test command passes.
- `grep -n "MANUAL_NUDGE_INTERVAL\|is_upcoming_reminder\|next_manual_nudge_time" training/*_views.py` finds no matches.
