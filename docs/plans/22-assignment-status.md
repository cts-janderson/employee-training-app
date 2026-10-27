# Slice 22: one assignment status

Part of [Deepen the training modules: round 3](deepen-modules-round-3.md). Do this slice first in round 3. [Slice 26](26-dashboard-report.md) builds on it. **Make the decision below before you start.**

## Problem

An assignment's status has three parts: its state (pending or its ending), its progress and its deadline status. [assignments.py](../../training/assignments.py) computes each part separately:

```python
def progress_status(assignment):          # assignments.py:95
def assignment_state(assignment):         # assignments.py:103
    return assignment_ending(assignment) or "pending"
def deadline_status(assignment, *, now=None):  # assignments.py:107
```

`deadline_status` doesn't know about endings. A canceled or waived assignment that is past due comes back `"overdue"`. So each caller adds its own rule, "a canceled or waived assignment has no deadline status", and its own label lookup:

| Where | Copy |
|---|---|
| `report_rows`, [assignment_reports.py:133-140](../../training/assignment_reports.py) | `deadline = None if state in {"canceled", "waived"} else deadline_status(assignment, now=now)`, then `DEADLINE_LABELS[deadline] if deadline else "—"` and `PROGRESS_LABELS[progress_status(assignment)]` |
| `report_rows`, line 138 | `state=f"Archived · {state.title()}" if assignment.archived_at else state.title()` |
| `report_assignments`, [assignment_reports.py:78-103](../../training/assignment_reports.py) | `report_progress`, `report_deadline` and `report_rank` SQL annotations. The comment says "These SQL labels mirror progress_status() and deadline_status()". `report_deadline` adds `When(ending_value__in=[Ending.CANCELED, Ending.WAIVED], then=Value(None))` |
| `_detail_context`, [assignment_admin_views.py:162-172](../../training/assignment_admin_views.py) | `DEADLINE_LABELS[deadline_status(assignment, now=now)] if state not in {"canceled", "waived"} else "—"`, and `state.title()` |
| `my_training`, [assignment_views.py:228-240](../../training/assignment_views.py) | `DEADLINE_LABELS[deadline_code]` with no ending check, and groups rows with `waived if assignment.waived_at else completed if assignment.completed_at else unfinished` |
| `assignment_detail`, [assignment_views.py:271](../../training/assignment_views.py) | `"—" if assignment.waived_at else DEADLINE_LABELS[deadline_status(assignment, now=now)]` |
| `quiz_result`, [quiz_learner_views.py:177](../../training/quiz_learner_views.py) | `DEADLINE_LABELS[deadline_status(assignment, now=now)]` with **no** ending check |
| `pending_load_chart`, [admin_chart.py:31-35](../../training/admin_chart.py) | Reads the report's annotation names: `f"{cell['report_deadline']}_{cell['report_progress']}"` |

`AssignmentQuerySet.in_state` ([models.py:269-279](../../training/models.py)) also says it mirrors `assignment_state()`. It filters on the Slice 18 ending, so it doesn't need to change.

### A defect

`quiz_result` lets waived assignments through (`if denial and denial != LearnerDenial.WAIVED`, line 170). A waived assignment can have a failed quiz attempt from before the waiver. If it is past due, the result page shows `Due … · Overdue`. The learner's assignment page shows `Deadline: —` for the same assignment. `my_training` computes "Overdue" for waived rows too, but its template doesn't show the deadline for waived rows, so nothing visible is wrong there.

### The tests

`test_sql_progress_deadline_and_order_match_assignment_rules` in [test_assignment_reports.py:46-65](../../training/test_assignment_reports.py) re-derives the rule inside the test: `expected = "—" if row.assignment.canceled_at else DEADLINE_LABELS[...]`. It has no waived fixture. `test_dashboard_export_and_constraint_treat_waiver_as_its_own_state` in [test_waivers.py](../../training/test_waivers.py) checks one waived report row (`("Waived", "—")`). No test checks that the SQL annotations and the Python rules agree for every combination.

## Decision needed

What should `quiz_result` show in place of the deadline status for a waived assignment? This is the slice's one behaviour change.

- **A. "—" (recommended).** The line reads `Due 2026-10-01 (UTC) · —`. This matches the learner's assignment page, the administrator's detail page, the report and the CSV.
- **B. Leave out the ` · {{ deadline }}` part.** This needs a template condition in [quiz_result.html](../../templates/training/quiz_result.html), so the page gets its own rule again.
- **C. Show "Waived".** This mixes the state into the deadline slot, which no other page does.

**Decision: Option A**, recorded 2026-10-27. A waived assignment's quiz result shows "—" in place of the deadline status.

## Change

### 1. One status value

Add this to [assignments.py](../../training/assignments.py), next to `progress_status` and `deadline_status`:

```python
STATE_LABELS = {"pending": "Pending", **dict(Ending.choices)}


@dataclass(frozen=True)
class AssignmentStatus:
    state: str             # "pending" or an Ending value
    progress: str          # a PROGRESS_LABELS key
    deadline: str | None   # a DEADLINE_LABELS key; None after cancellation or waiver
    archived: bool

    @property
    def state_label(self): ...           # "Pending", "Completed", "Canceled", "Waived"
    @property
    def report_state_label(self): ...    # "Archived · Canceled" when archived, else state_label
    @property
    def progress_label(self): ...
    @property
    def deadline_label(self): ...        # "—" when deadline is None


def assignment_status(assignment, *, now=None):
    """The state, progress and deadline codes shown for an assignment."""
```

- `state` is `assignment_state(assignment)`. `progress` is `progress_status(assignment)`.
- `deadline` is `None` when the ending is `Ending.CANCELED` or `Ending.WAIVED`. Otherwise it is `deadline_status(assignment, now=now)`. A completed assignment keeps `"on_time"` or `"completed_late"`, even after archiving.
- `STATE_LABELS` gives the same text as today's `state.title()`: the `Ending` labels in [models.py](../../training/models.py) are `"Completed"`, `"Canceled"` and `"Waived"`.
- Keep `progress_status`, `deadline_status`, `assignment_state`, `PROGRESS_LABELS` and `DEADLINE_LABELS`. Existing tests call and import them, and `ReportFilters.from_params` validates filter values against the label keys.

The status is not a new domain concept. It groups the existing state, progress and deadline status. Don't add it to the glossary.

### 2. The same status in SQL

Add this to `AssignmentQuerySet` in [models.py](../../training/models.py), next to `with_ending`:

```python
def with_status(self, *, now):
    """Annotate status_state, status_progress and status_deadline, mirroring assignment_status()."""
```

- `status_state` is the ending, or `"pending"` when there is none (`Coalesce` over `ending_value`).
- `status_progress` and `status_deadline` are today's `report_progress` and `report_deadline` `Case` expressions, moved unchanged. `status_deadline` is `NULL` for canceled and waived assignments.
- It calls `with_ending()` itself. Annotating `ending_value` twice is safe. `report_assignments` already does it today, through `in_state`.
- The `Case` stays in [models.py](../../training/models.py) because `models.py` can't import `assignments.py`. Each copy gets a one-line comment naming the other. The table test in [Tests](#tests) is what keeps them in step.

### 3. The report uses both

In [assignment_reports.py](../../training/assignment_reports.py):

- `report_assignments` calls `.with_status(now=now)` instead of annotating `report_progress` and `report_deadline`. It filters on `status_progress` and `status_deadline`.
- `report_rank` stays in `report_assignments`, because it is the report's sort order. Build it from `status_state` and `status_deadline`. Keep the same ranks: archived 3, pending and overdue 0, pending 1, completed 2, anything else 3.
- `report_rows` calls `assignment_status(assignment, now=now)` once per row. It uses `status.report_state_label`, `status.progress_label` and `status.deadline_label`. Delete its `state in {"canceled", "waived"}` check.
- `report_rows` keeps computing in Python rather than reading the annotations. It also works on assignments that weren't loaded through `with_status`.
- Remove `DEADLINE_LABELS`, `PROGRESS_LABELS`, `deadline_status` and `progress_status` from the imports if nothing else in the module uses them. `ReportFilters.from_params` still uses the label keys.

### 4. The pending chart reads the new names

In `pending_load_chart` ([admin_chart.py](../../training/admin_chart.py)), read `status_deadline` and `status_progress` instead of `report_deadline` and `report_progress`. Change only those names, in `values(...)` and in the group key.

Do this here, not in Slice 26. Leaving the old names would mean keeping a second pair of annotations just for the chart. Keep the `ValueError` for an unexpected group. [scripts/render-admin-chart-preview.py](../../scripts/render-admin-chart-preview.py) calls `report_assignments` and `pending_load_chart` together, so it needs no change.

### 5. Views use the status

- `_detail_context`: `status = assignment_status(assignment, now=now)`. Then `"state": status.state_label`, `"is_pending": status.state == "pending"`, `"progress": status.progress_label`, `"deadline": status.deadline_label`. Move `now = timezone.now()` above the first use.
- `assignment_detail`: `"progress"` and `"deadline"` come from `assignment_status(assignment, now=now)`. Delete the `assignment.waived_at` check.
- `my_training`:
  - `"progress"` is `status.progress_label`, `"deadline"` is `status.deadline_label`, and `"deadline_code"` is `status.deadline`.
  - Group by `status.state`: waived rows go to `waived`, completed rows to `completed`, and the rest to `unfinished`. `visible_to_learner` already excludes canceled assignments.
  - Keep the template context keys and the sort keys unchanged.
- `quiz_result`: `"deadline": assignment_status(assignment, now=now).deadline_label`. This is the behaviour change. A waived assignment shows "—".

Remove the label and status-function imports these modules no longer use.

## Out of scope

- `AssignmentQuerySet.in_state`. It filters on the shared ending, so it can't drift from `assignment_state`.
- The dashboard view's mode handling, filter echo and chart choice. [Slice 26](26-dashboard-report.md) covers them.
- `quiz_outcomes_chart` and the quiz score columns in `report_rows`. Slice 26 covers them.
- Templates that compare labels, such as `row.deadline == 'Overdue'` in [assignment_dashboard.html](../../templates/training/assignment_dashboard.html) and `row.deadline == 'Completed late'` in [my_training.html](../../templates/training/my_training.html). They keep working because the labels don't change.
- Learner visibility. `visible_to_learner` keeps excluding canceled assignments by name, as Slice 18 decided.

## Tests

Add `training/test_assignment_status.py`:

- `test_python_and_sql_status_agree`: a table-driven test with one subtest per row. Create each assignment for its own employee, so ADR-0001's one-outstanding constraint never applies.
  - Ending: none, completed, canceled, waived, and completed then canceled.
  - Started or not.
  - Past due or not. For completed work, "past due" means `completed_at >= due_at`.
  - Archived or not, for every ending except none. Archiving unfinished work cancels it ([ADR-0004](../adr/0004-archive-assignment-without-deleting-evidence.md)), so there is no archived pending row.
  - For each row, assert that `assignment_status(assignment, now=now)` equals the `(status_state, status_progress, status_deadline)` values from `Assignment.objects.with_status(now=now)`, plus `archived_at is not None`.
  - Also assert the expected codes from the table. `deadline` is `None` for every canceled and waived row. A completed-then-canceled row has progress `"completed"` and deadline `None`.
- `test_report_rows_use_status_labels`: for the same table, `report_rows` gives `report_state_label`, `progress_label` and `deadline_label`. Include an `"Archived · Waived"` row.
- `test_due_at_boundary_is_overdue_in_python_and_sql`: with `due_at == now`, both say `"overdue"`.
- `test_quiz_result_shows_no_deadline_status_for_waived_work`: create a past-due assignment with a failed attempt, waive it, switch to the learner and open `quiz_result`. Assert status 200, `assertNotContains(response, "Overdue")` and `assertContains(response, "· —")`.

All existing tests must pass unchanged. `test_sql_progress_deadline_and_order_match_assignment_rules` keeps its own derivation. Don't edit it. The new table test replaces it as the real check.

## Done when

- The test command passes with 4 more tests than the baseline: 191 tests, OK (skipped=3).
- `makemigrations --check --dry-run` prints "No changes detected".
- These searches find nothing outside `assignments.py` and the tests:

  ```bash
  grep -n "PROGRESS_LABELS\[\|DEADLINE_LABELS\[" training/*.py | grep -v "training/test_\|training/assignments.py"
  grep -n "deadline_status(\|progress_status(" training/*.py | grep -v "training/test_\|training/assignments.py"
  ```

- These searches find no matches:

  ```bash
  grep -n 'in {"canceled", "waived"}' training/*.py               # the per-caller ending rule
  grep -n "assignment.waived_at" training/*_views.py              # the waived checks in the learner views
  grep -n "report_progress\|report_deadline" training/*.py scripts/*.py
  ```

- `grep -n "state.title()" training/*.py` finds no matches.
