# Slice 13: assignment report

Part of [Extended user stories](extended-stories.md). A pure refactor that `US-13`, `US-16` and `US-18` build on.

## Problem

`assignment_dashboard` in [assignment_admin_views.py](../../training/assignment_admin_views.py) is the only definition of what the dashboard reports, and it lives in a view. It's about 120 lines that:

- validate six filters by hand against `STATE_VALUES`, `ACTIVITY_VALUES`, `PROGRESS_LABELS` and `DEADLINE_LABELS`;
- filter by archive, state, activity, employee and training **in SQL**;
- filter by progress and deadline **in Python**, after the query, by calling `progress_status` and `deadline_status` row by row;
- work out the latest and passing scores, and the "N/A" vs "—" rule for trainings without a quiz;
- give each row a private `_sort_rank`, sort in Python, then delete the key.

`US-13` says the export must use "the same authoritative assignment and attempt records as the dashboard, including the same treatment of inactive employees and archived assignments". Copying the view would give two definitions that can drift apart. `US-16` needs a total count and pages. Because half the filtering and all the sorting happen in Python, Django's `Paginator` can't page the queryset. `US-18` adds a state, which today would mean editing the view's filter checks, its rank expression and its state label.

The deletion test: if the report logic were a module, deleting it would make the complexity reappear in the dashboard, the export and the paged list. So it should be a module.

## Change

### 1. A new `assignment_reports.py` module

Create [training/assignment_reports.py](../../training/assignment_reports.py) with this interface:

```python
class ReportFilterError(ValueError):
    """A filter value is invalid. str() is the message the dashboard shows today."""

@dataclass(frozen=True)
class ReportFilters:
    state: str = "pending"        # pending | completed | canceled | archived | all
    activity: str = "active"      # active | inactive | all
    progress: str = "all"         # not_started | in_progress | completed | all
    deadline: str = "all"         # on_time | overdue | completed_late | all
    employee_id: uuid.UUID | None = None
    training_id: uuid.UUID | None = None

    @classmethod
    def from_params(cls, params) -> "ReportFilters":
        """Parse a QueryDict-like mapping. Keep today's defaults, including activity="all"
        when state="archived". Raise ReportFilterError with today's messages."""

    def as_params(self) -> dict:
        """The filter values as query-string parameters, so other pages can link back."""

def report_assignments(filters, *, now) -> QuerySet[Assignment]:
    """Return every assignment the filters select, in report order, with every filter applied in SQL."""

@dataclass(frozen=True)
class ReportRow:
    assignment: Assignment
    state: str            # "Pending", "Archived · Completed", …
    progress: str         # label from PROGRESS_LABELS
    deadline: str         # label from DEADLINE_LABELS, or "—" for canceled work
    latest_score: str     # "85.0%", "—" or "N/A"
    passing_score: str

def report_rows(assignments, *, now) -> list[ReportRow]:
    """Project an iterable of report assignments, such as one page, into display rows."""
```

Notes on the implementation:

- `report_assignments` starts from `Assignment.objects.filter(employee__is_demo=True)` and does today's `select_related`/`prefetch_related`. Use `.order_by("submitted_at")` in a `Prefetch` for `quiz_attempts`, so `report_rows` doesn't sort in Python.
- **Progress and deadline move into SQL.** Annotate them with `Case`/`When`, using the same rules as `progress_status()` and `deadline_status(now=now)` in [assignments.py](../../training/assignments.py). Canceled assignments get no deadline annotation (`None`). That keeps today's behaviour: a canceled row never matches a deadline filter other than `all`.
- **The rank moves into SQL.** Annotate `report_rank` with today's expression: 0 for pending and overdue, 1 for pending, 2 for completed, 3 for canceled or archived. Order by `report_rank`, `due_at`, `assigned_at`, `id`. `id` is new. It only breaks ties that are arbitrary today, so pages stay stable (needed in Slice 15).
- Keep `assignment_state()`, `progress_status()` and `deadline_status()` as the per-object functions. Put a comment by the annotations saying they mirror those functions, as `in_state()` already does. Slice 18 makes the state rule a single definition.
- `report_rows` holds the score and label logic now in the view: `displayed_score`, the latest attempt, the first passing attempt and the "N/A" rule.

### 2. The dashboard uses the module

`assignment_dashboard` becomes: parse `ReportFilters.from_params(request.GET)`, catch `ReportFilterError` (status 400, as today), call `report_assignments`, then `report_rows`, attach the follow-up summaries and render. The follow-up summaries stay in the view, because the export doesn't need them. The employee and training option lists and `active_filters` stay in the view.

Pass the template the same context keys. Rows are now objects rather than dicts, so check the template reads them as attributes. Django resolves `row.state` the same way in both cases. `row.manual_next_at` and the other follow-up fields need a small wrapper or a dict, so use `{"report": row, **summary_fields}` only if the template must change. Prefer leaving the template unchanged.

Use one `now = timezone.now()` for the whole request, as [Slice 9](09-assignment-queries-and-labels.md) established.

### 3. Remove the leftovers

- Delete `STATE_VALUES`, `ACTIVITY_VALUES` and `_uuid_filter` from the view module.
- `in_state()` stays on the queryset. `report_assignments` calls it.

## Tests

Add `training/test_assignment_reports.py`. Test through the interface only:

- `test_filters_reject_unknown_values_with_dashboard_messages`.
- `test_archived_state_defaults_activity_to_all`.
- `test_sql_progress_and_deadline_match_per_assignment_rules`: build a fixture covering not started, in progress, completed on time, completed late, overdue, canceled and archived. Check that every `report_rows` value agrees with `progress_status()` and `deadline_status()` for its assignment.
- `test_report_order_is_overdue_pending_completed_then_ended`.
- `test_report_order_is_total`: two assignments with the same `due_at` and `assigned_at` come back in the same order every time.
- `test_scores_show_na_without_quiz_and_dash_before_first_attempt`.

The existing dashboard tests in `test_assignment_admin.py` and `test_assignment_archive.py` must pass unchanged.

## Out of scope

- Paging and search (Slice 15).
- The waived state (Slice 19).
- Moving the follow-up summary into the report.
