import uuid
from dataclasses import dataclass, replace
from itertools import islice
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.db.models import Case, IntegerField, Prefetch, Q, Value, When
from django.db.models.functions import Lower

from .assignments import DEADLINE_LABELS, PROGRESS_LABELS, assignment_status
from .admin_chart import pending_load_chart
from .models import Assignment, Employee, Ending, QuizAttempt, Training
from .notifications import follow_up_summaries
from .population import employees as employees_in, trainings as trainings_in
from .quiz_outcomes_chart import quiz_outcomes_chart
from .quizzes import displayed_score, quiz_record


PENDING_MODE, QUIZ_MODE = "pending", "quiz"
QUIZ_OUTCOME_SCOPE = {"state": "all", "progress": "all", "deadline": "all"}


class ReportFilterError(ValueError):
    pass


@dataclass(frozen=True)
class ReportFilters:
    state: str = "pending"
    activity: str = "active"
    progress: str = "all"
    deadline: str = "all"
    employee_id: uuid.UUID | None = None
    training_id: uuid.UUID | None = None
    q: str = ""

    @staticmethod
    def form_values(params):
        return {
            "state": params.get("state", "pending"),
            "activity": params.get("activity", "all" if params.get("state") == "archived" else "active"),
            "progress": params.get("progress", "all"), "deadline": params.get("deadline", "all"),
            "employee": params.get("employee", ""), "training": params.get("training", ""),
        }

    @classmethod
    def from_params(cls, params):
        form = cls.form_values(params)
        state = form["state"]
        activity = form["activity"]
        progress = form["progress"]
        deadline = form["deadline"]
        if (state not in {"pending", "completed", "canceled", "waived", "archived", "all"}
                or activity not in {"active", "inactive", "all"}
                or progress not in {*PROGRESS_LABELS, "all"}
                or deadline not in {*DEADLINE_LABELS, "all"}):
            raise ReportFilterError("Choose a valid dashboard filter.")

        def parse_id(key):
            value = params.get(key, "")
            if not value:
                return None
            try:
                return uuid.UUID(str(value))
            except (ValueError, TypeError, AttributeError):
                raise ReportFilterError("Choose a valid employee or training filter.") from None

        query = " ".join(params.get("q", "").split())[:200]
        return cls(state, activity, progress, deadline, parse_id("employee"), parse_id("training"), query)

    def as_params(self):
        return {
            "state": self.state, "activity": self.activity, "progress": self.progress,
            "deadline": self.deadline, "employee": str(self.employee_id) if self.employee_id else "",
            "training": str(self.training_id) if self.training_id else "",
            "q": self.q,
        }


@dataclass(frozen=True)
class DashboardRequest:
    mode: str
    filters: ReportFilters | None
    error: str | None
    form_values: dict

    @property
    def browse_params(self):
        return {**self.filters.as_params(), **({"mode": QUIZ_MODE} if self.mode == QUIZ_MODE else {})}

    @property
    def shows_pending_chart(self):
        return self.error is None and self.mode == PENDING_MODE and self.filters.state == "pending"

    def mode_params(self, mode):
        shared = {key: self.form_values.get(key, "") for key in ("activity", "employee", "training", "q")}
        return {**shared, **({"mode": QUIZ_MODE} if mode == QUIZ_MODE else {"state": "pending"})}


def dashboard_request(params):
    mode = QUIZ_MODE if params.get("mode") == QUIZ_MODE else PENDING_MODE
    try:
        scoped = params.copy()
        if mode == QUIZ_MODE:
            scoped.update(QUIZ_OUTCOME_SCOPE)
        filters = ReportFilters.from_params(scoped)
    except ReportFilterError as exc:
        return DashboardRequest(mode, None, str(exc), ReportFilters.form_values(params))
    return DashboardRequest(mode, filters, None, filters.as_params())


def dashboard_report(dashboard, assignments, page_rows, *, is_demo, now):
    """Build the dashboard context from a filtered query and the current page."""
    filters = dashboard.form_values
    rows = [{"assignment": row.assignment, "state": row.state, "progress": row.progress,
             "deadline": row.deadline, "latest_score": row.latest_score,
             "passing_score": row.passing_score} for row in page_rows]
    employees = employees_in(is_demo).order_by("name")
    trainings = trainings_in(is_demo).filter(assignments__isnull=False).distinct().with_display_title().order_by(
        Lower("display_title"), "id",
    )
    training_rows = []
    for training in trainings:
        label = training.display_title
        training_rows.append({"id": training.id, "label": label,
                              "selected": filters["training"] == str(training.id)})
    labels = {row["id"]: row["label"] for row in training_rows}
    chart = (pending_load_chart(assignments, labels) if dashboard.shows_pending_chart else None)
    quiz_chart = (quiz_outcomes_chart(assignments, labels)
                  if not dashboard.error and dashboard.mode == QUIZ_MODE else None)
    if rows:
        summaries = follow_up_summaries([row["assignment"] for row in rows], now=now)
        for row in rows:
            summary = summaries[row["assignment"].id]
            row["manual_next_at"] = summary.manual_next_at
            row["manual_can_send"] = summary.manual_can_send
            row["next_scheduled_reminder"] = summary.next_scheduled_reminder
    active_filters = []
    if dashboard.mode != QUIZ_MODE:
        if filters["state"] != "pending":
            active_filters.append(f"State: {filters['state'].replace('_', ' ').title()}")
        if filters["progress"] != "all":
            active_filters.append(f"Progress: {filters['progress'].replace('_', ' ').title()}")
        if filters["deadline"] != "all":
            active_filters.append(f"Deadline: {filters['deadline'].replace('_', ' ').title()}")
    if filters["activity"] != "active":
        active_filters.append(f"Activity: {filters['activity'].replace('_', ' ').title()}")
    if filters["employee"]:
        selected_employee = next((employee.name for employee in employees
                                  if str(employee.id) == filters["employee"]), "Unknown employee")
        active_filters.append(f"Employee: {selected_employee}")
    if filters["training"]:
        selected_training = next((row["label"] for row in training_rows if row["selected"]), "Unknown training")
        active_filters.append(f"Training: {selected_training}")
    if not dashboard.error and dashboard.filters.q:
        active_filters.append(f"Search: {dashboard.filters.q}")
    return {
        "rows": rows, "filters": filters,
        "employees": [{"employee": employee, "selected": filters["employee"] == str(employee.id)}
                      for employee in employees],
        "trainings": training_rows, "error": dashboard.error,
        "active_filters": active_filters,
        "filter_query": urlencode(dashboard.filters.as_params()) if not dashboard.error else "",
        "chart": chart, "quiz_chart": quiz_chart, "chart_mode": dashboard.mode,
        "show_pending_chart": dashboard.shows_pending_chart,
    }


def report_assignments(filters, *, is_demo, now):
    assignments = Assignment.objects.filter(employee__is_demo=is_demo).with_status(now=now).select_related(
        "employee", "training", "version", "version__quiz",
    ).prefetch_related(Prefetch("quiz_attempts", queryset=QuizAttempt.objects.order_by("submitted_at")))
    assignments = assignments.filter(archived_at__isnull=filters.state != "archived")
    if filters.state in {"pending", "completed", "canceled", "waived"}:
        assignments = assignments.in_state(filters.state)
    if filters.activity != "all":
        assignments = assignments.filter(employee__is_active=filters.activity == "active")
    if filters.employee_id:
        assignments = assignments.filter(employee_id=filters.employee_id)
    if filters.training_id:
        assignments = assignments.filter(training_id=filters.training_id)
    if filters.q:
        assignments = assignments.filter(Q(employee__name__icontains=filters.q)
                                         | Q(employee__email__icontains=filters.q)
                                         | Q(version__title__icontains=filters.q))

    if filters.progress != "all":
        assignments = assignments.filter(status_progress=filters.progress)
    if filters.deadline != "all":
        assignments = assignments.filter(status_deadline=filters.deadline)
    return assignments.annotate(report_rank=Case(
        When(archived_at__isnull=False, then=Value(3)),
        When(status_state="pending", status_deadline="overdue", then=Value(0)),
        When(status_state="pending", then=Value(1)),
        When(status_state=Ending.COMPLETED, then=Value(2)),
        default=Value(3), output_field=IntegerField(),
    )).order_by("report_rank", "due_at", "assigned_at", "id")


def quiz_outcome_assignments(filters, *, is_demo, now):
    """Use the shared people, training, and search filters across quiz cycles."""
    scope = replace(filters, **QUIZ_OUTCOME_SCOPE)
    return report_assignments(scope, is_demo=is_demo, now=now).filter(
        Q(ending_value__isnull=True) | Q(ending_value=Ending.COMPLETED),
        version__quiz__isnull=False,
    )


@dataclass(frozen=True)
class ReportRow:
    assignment: Assignment
    cycle_number: int
    state: str
    progress: str
    deadline: str
    latest_score: str
    passing_score: str


def report_rows(assignments, *, now):
    rows = []
    for assignment in assignments:
        record = quiz_record(assignment)
        latest, passing = record.latest, record.passing
        has_quiz = hasattr(assignment.version, "quiz")
        status = assignment_status(assignment, now=now)
        rows.append(ReportRow(
            assignment=assignment,
            cycle_number=assignment.cycle_number,
            state=status.report_state_label,
            progress=status.progress_label,
            deadline=status.deadline_label,
            latest_score=f"{displayed_score(latest)}%" if latest else ("—" if has_quiz else "N/A"),
            passing_score=f"{displayed_score(passing)}%" if passing else ("—" if has_quiz else "N/A"),
        ))
    return rows


EXPORT_COLUMNS = (
    "Generated at", "Time zone", "Employee", "Employee email", "Employee activity", "Training",
    "Version", "Cycle", "State", "Due date", "Progress", "Deadline status", "Completed at",
    "Latest score", "Passing score", "Assignment ID",
)


def _spreadsheet_safe(value):
    value = str(value)
    return f"'{value}" if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def export_rows(filters, *, is_demo, now, zone_name):
    zone = ZoneInfo(zone_name)
    generated_at = now.astimezone(zone).isoformat()
    yield list(EXPORT_COLUMNS)
    assignments = report_assignments(filters, is_demo=is_demo, now=now).iterator(chunk_size=500)
    while batch := list(islice(assignments, 500)):
        for row in report_rows(batch, now=now):
            assignment = row.assignment
            cells = (
                generated_at, zone_name, assignment.employee.name, assignment.employee.email,
                "Active" if assignment.employee.is_active else "Inactive", assignment.version.title,
                assignment.version.version_number, row.cycle_number, row.state, assignment.due_date.isoformat(),
                row.progress, row.deadline,
                assignment.completed_at.astimezone(zone).isoformat() if assignment.completed_at else "",
                row.latest_score, row.passing_score, assignment.id,
            )
            yield [_spreadsheet_safe(value) for value in cells]
