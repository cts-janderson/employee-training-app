# Slice 26: dashboard report

Part of [Deepen the training modules: round 3](deepen-modules-round-3.md). Do [Slice 22](22-assignment-status.md) first. This slice relies on its status annotations and labels.

## Problem

`assignment_dashboard` in [assignment_admin_views.py:31-122](../../training/assignment_admin_views.py) is about 90 lines. It knows the report's rules, not just how to render them:

| Rule | Copies |
|---|---|
| Quiz mode ignores the state, progress and deadline filters | The view sets `params["state"] = "all"`, `params["progress"] = "all"` and `params["deadline"] = "all"` (lines 41-44). `quiz_outcome_assignments` does it again with `replace(filters, state="all", progress="all", deadline="all")` ([assignment_reports.py:108](../../training/assignment_reports.py)) |
| Filter defaults | `ReportFilters.from_params` (lines 29-32) and the view's error fallback (lines 60-65) both write `"all" if ... == "archived" else "active"` and the other defaults |
| Which chart to show | Lines 76-81 choose `quiz_outcomes_chart` or `pending_load_chart`. Line 121 repeats the pending condition for `show_pending_chart` |
| Active-filter labels | Lines 89-107, including which filters quiz mode hides |
| Training menu labels | Lines 69-75: the latest published title, or the training id |
| Report rows | Lines 53-56 copy each `ReportRow` into a dict. Lines 82-88 then add the follow-up fields |

`pending_load_chart` ([admin_chart.py](../../training/admin_chart.py)) raises `ValueError` for any group that isn't pending, so the view's state check is the only thing keeping it safe.

The quiz attempt rules also have two copies:

| Where | Latest attempt | Passing |
|---|---|---|
| `report_rows`, [assignment_reports.py:129-131](../../training/assignment_reports.py) | `attempts[-1]`, in the prefetch's `order_by("submitted_at")` order | `next((attempt for attempt in attempts if attempt.passed), None)` |
| `quiz_outcomes_chart`, [quiz_outcomes_chart.py:35-38](../../training/quiz_outcomes_chart.py) | `max(attempts, key=lambda attempt: (attempt.submitted_at, str(attempt.id)))` | `any(attempt.passed for attempt in attempts)` |

They differ only when two attempts have the same `submitted_at`. Then the chart picks the larger id, and the report picks whichever row the database returns last.

### What the existing tests pin

- `test_due_at_request_clock_is_overdue_in_chart_and_list` in [test_admin_chart.py:124-138](../../training/test_admin_chart.py) patches `training.assignment_admin_views.report_assignments` and `training.assignment_admin_views.report_rows`. It asserts both were called with the request's `now`. So the view must keep calling those two functions by name from its own module. A single `dashboard(params, *, now)` that runs the query inside [assignment_reports.py](../../training/assignment_reports.py) would fail this test.
- Several tests read `response.context["rows"]` as dicts: `row["assignment"]` and `row["deadline"]`. The rows stay dicts.
- `test_queryset_scope_excludes_waived_and_preserves_search` in [test_quiz_outcomes_chart.py](../../training/test_quiz_outcomes_chart.py) calls `quiz_outcome_assignments` with `state="pending"` and expects completed work. So `quiz_outcome_assignments` keeps its own reset.
- Tests call `pending_load_chart(queryset, labels)` and `quiz_outcomes_chart(queryset, labels)` directly. Keep both signatures.

## Change

### 1. One quiz record per assignment

Add this to [quizzes.py](../../training/quizzes.py), next to `displayed_score`:

```python
@dataclass(frozen=True)
class QuizRecord:
    latest: QuizAttempt | None
    passing: QuizAttempt | None   # the first passing attempt
    outcome: str                  # "passed", "retry_needed" or "not_attempted"


def quiz_record(assignment):
    """Order the assignment's attempts by (submitted_at, id), whatever order they were loaded in."""
```

- Read `assignment.quiz_attempts.all()`, so the report's prefetch still serves it. Sort in Python by `(submitted_at, id)`.
- `outcome` is `"passed"` when there is a passing attempt, `"retry_needed"` when there are attempts but none passed, and `"not_attempted"` otherwise. This is the chart's rule today.
- `report_rows` uses `record.latest` and `record.passing`. Keep its `"—"` and `"N/A"` choice as it is.
- `quiz_outcomes_chart` uses `record.latest` and `record.outcome`. `OUTCOMES` and `OUTCOME_LABELS` stay in [quiz_outcomes_chart.py](../../training/quiz_outcomes_chart.py), because they are chart text.

This is the slice's one behaviour change. When two attempts share a `submitted_at`, the report's latest and passing scores now follow the chart's order. Today the report's order for such ties is whatever the database returns, so there is no contract to keep. It needs no decision.

The `Prefetch` in `report_assignments` can keep `order_by("submitted_at")`.

### 2. One quiz scope

Add this to [assignment_reports.py](../../training/assignment_reports.py):

```python
PENDING_MODE, QUIZ_MODE = "pending", "quiz"
QUIZ_OUTCOME_SCOPE = {"state": "all", "progress": "all", "deadline": "all"}
```

- `quiz_outcome_assignments` uses `replace(filters, **QUIZ_OUTCOME_SCOPE)`.
- The dashboard request (step 4) applies the same values to a copy of the params before validation. That keeps today's behaviour: in quiz mode, an invalid `progress` value is ignored, not an error.

### 3. One set of filter defaults

```python
class ReportFilters:
    @staticmethod
    def form_values(params):
        """state, activity, progress, deadline, employee and training as submitted, with the report defaults."""
```

- `from_params` reads its raw values from `form_values(params)` and validates them as today.
- The error fallback uses `form_values` on the **unmodified** request params. Today it reads `request.GET` directly, not the quiz-mode copy, so in quiz mode an error still echoes the submitted state. Keep that.
- `form_values` returns the six keys the error fallback has today. It has no `q` key, so the search box and the search label stay empty on error, as today.

### 4. The dashboard request

```python
@dataclass(frozen=True)
class DashboardRequest:
    mode: str                      # PENDING_MODE or QUIZ_MODE
    filters: ReportFilters | None  # None when the filters are invalid
    error: str | None
    form_values: dict              # filters.as_params(), or ReportFilters.form_values() on error

    @property
    def browse_params(self): ...   # filters.as_params(), plus "mode": "quiz" in quiz mode
    @property
    def shows_pending_chart(self): ...  # no error, pending mode, state "pending"
    def mode_params(self, mode): ...    # activity, employee, training and q, plus state=pending or mode=quiz


def dashboard_request(params):
    """Parse the mode and filters. A ReportFilterError becomes .error, never an exception."""
```

`mode` is `QUIZ_MODE` only when `params.get("mode") == "quiz"`, as today.

### 5. The dashboard report

```python
def dashboard_report(dashboard, assignments, page_rows, *, now):
    """Return the template context except browse, the URLs and org_time_zone."""
```

It takes the full filtered queryset (or `None` on error) and the current page's `ReportRow` list. It returns today's keys, with today's values:
- `rows`: one dict per `ReportRow`, with today's keys, plus the follow-up fields from `follow_up_summaries(..., now=now)`.
- `filters`: `dashboard.form_values`.
- `employees` and `trainings`: the menu options, moved unchanged from the view, including the latest-published-title rule.
- `chart` and `quiz_chart`: `quiz_outcomes_chart(assignments, labels)` in quiz mode, `pending_load_chart(assignments, labels)` when `dashboard.shows_pending_chart`, otherwise `None`. This is now the only place that checks the pending condition before calling `pending_load_chart`.
- `active_filters`: today's labels in today's order. Quiz mode leaves out state, progress and deadline. `"Unknown employee"` and `"Unknown training"` stay the same.
- `chart_mode`, `error`, `show_pending_chart` and `filter_query`.

Keep every label character for character. For example, `f"State: {filters['state'].replace('_', ' ').title()}"`.

### 6. The view only wires and renders

`assignment_dashboard` becomes roughly:

```python
now = timezone.now()
dashboard = dashboard_request(request.GET)
assignments, listing, rows = None, None, []
if not dashboard.error:
    query = quiz_outcome_assignments if dashboard.mode == QUIZ_MODE else report_assignments
    assignments = query(dashboard.filters, now=now)
    listing = browse(request, assignments, search_fields=(), extra_params=dashboard.browse_params)
    rows = report_rows(listing.page.object_list, now=now)
context = dashboard_report(dashboard, assignments, rows, now=now)
# render with browse, org_time_zone and the two mode URLs built from request.path and mode_params()
```

- The view keeps calling `report_assignments` and `report_rows` from its own module, because the existing test patches them there. Add a one-line comment saying so.
- Paging stays in the view, because `browse` needs the request.
- Status 400 on error, 200 otherwise, as today.

### 7. Keep the two chart modules apart

Don't merge [admin_chart.py](../../training/admin_chart.py) and [quiz_outcomes_chart.py](../../training/quiz_outcomes_chart.py). They scale and sort differently: the pending chart scales to the largest training, and the quiz chart scales each bar to 100%. This slice keeps one dashboard path with two chart modes. That stays consistent with [the chart plan](21-admin-assignment-chart.md), which completes the existing integration "rather than adding a second chart path".

`pending_load_chart` already reads Slice 22's `status_deadline` and `status_progress` annotations. Don't change it here.

## Out of scope

- Merging the chart modules, or changing their layout, colors, scaling or sort order.
- `export_assignments`. It already validates through `ReportFilters.from_params` and returns 400 on its own.
- The `Prefetch` ordering, and the report's SQL. Slice 22 owns the status annotations.
- The dashboard template. Context keys and values don't change.
- The menu queries for employees and trainings. They move unchanged and aren't optimised.

## Tests

Keep every existing render test. Each chart keeps at least one `client.get` test: `test_dashboard_renders_chart_for_pending_work` and `test_mode_classifies_retries_and_lists_people_behind_every_count`.

Add `training/test_dashboard_report.py`, calling the domain functions directly:

- `test_quiz_record_breaks_submitted_at_ties_by_id`: two attempts with the same `submitted_at`. `latest` is the larger id. A pass and a fail give `"passed"`. No attempts gives `"not_attempted"`.
- `test_report_rows_and_quiz_chart_agree_on_latest_attempt`: with tied attempts, the report's latest score equals the chart's latest score for the same assignment.
- `test_quiz_mode_ignores_state_progress_and_deadline`: `dashboard_request({"mode": "quiz", "progress": "bogus", "state": "completed"})` has no error, and its filters use `QUIZ_OUTCOME_SCOPE`.
- `test_invalid_filters_echo_submitted_values_with_defaults`: `{"state": "archived", "deadline": "bogus"}` gives the dashboard error message and `form_values["activity"] == "all"`. In quiz mode with a bad employee id, `form_values["state"]` is the submitted state.
- `test_pending_chart_only_for_valid_pending_requests`: `shows_pending_chart` is true for `{}`, and false for `{"state": "completed"}`, quiz mode and an invalid filter.
- `test_active_filter_labels_follow_mode`: in pending mode, state, progress, deadline, activity, employee, training and search all appear, in today's order. In quiz mode, the first three are left out. An unknown training id gives `"Training: Unknown training"`.

All existing tests must pass unchanged.

## Done when

- The test command passes. The count rises only by the 6 tests added here.
- `makemigrations --check --dry-run` prints "No changes detected".
- These searches find no matches:

  ```bash
  grep -n 'params\["state"\] = "all"\|state="all", progress="all"' training/*.py   # quiz scope copies
  grep -n "request.GET.get(\"state\"\|request.GET.get(\"activity\"" training/assignment_admin_views.py
  grep -n "active_filters.append\|pending_load_chart(\|quiz_outcomes_chart(" training/assignment_admin_views.py
  ```

- These searches each match only one file:

  ```bash
  grep -n "attempts\[-1\]\|max(attempts" training/*.py          # quizzes.py only
  grep -n '"all" if .*"archived"' training/*.py                  # assignment_reports.py only
  ```

- `assignment_dashboard` is about 25 lines.
