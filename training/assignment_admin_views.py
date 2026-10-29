import csv
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.http import HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .assignments import (
    AssignmentAccessError, AssignmentConflict, AssignmentInputError,
    archive_assignment, assignment_status, cancel_assignment, change_assignment_deadline, restore_assignment,
    waive_assignment,
)
from .assignment_reports import (
    PENDING_MODE, QUIZ_MODE, ReportFilterError, ReportFilters, dashboard_report,
    dashboard_request, export_rows, quiz_outcome_assignments, report_assignments, report_rows,
)
from .browsing import browse, safe_return_to
from .dev_auth import admin_required
from .models import Assignment, Quiz
from .notifications import (
    ManualNudgeIneligible, ManualNudgeRateLimited, follow_up_summaries, request_manual_nudge,
)
from .quizzes import displayed_score, submitted_answer_rows

@require_GET
@admin_required
def assignment_dashboard(request):
    now = timezone.now()
    dashboard = dashboard_request(request.GET)
    assignments = listing = None
    rows = []
    if not dashboard.error:
        query = quiz_outcome_assignments if dashboard.mode == QUIZ_MODE else report_assignments
        # These names stay in this view for callers that patch the request-clock seam.
        assignments = query(dashboard.filters, is_demo=request.employee.is_demo, now=now)
        listing = browse(request, assignments, search_fields=(), extra_params=dashboard.browse_params)
        rows = report_rows(listing.page.object_list, now=now)
    context = dashboard_report(dashboard, assignments, rows, is_demo=request.employee.is_demo, now=now)
    return render(request, "training/assignment_dashboard.html", {
        **context, "browse": listing, "org_time_zone": settings.ORG_TIME_ZONE,
        "pending_mode_url": f"{request.path}?{urlencode(dashboard.mode_params(PENDING_MODE))}",
        "quiz_mode_url": f"{request.path}?{urlencode(dashboard.mode_params(QUIZ_MODE))}",
    }, status=400 if dashboard.error else 200)


class _CSVBuffer:
    def write(self, value):
        return value


@require_GET
@admin_required
def export_assignments(request):
    try:
        filters = ReportFilters.from_params(request.GET)
    except ReportFilterError as exc:
        return HttpResponseBadRequest(str(exc))
    now = timezone.now()
    zone_name = settings.ORG_TIME_ZONE
    writer = csv.writer(_CSVBuffer())

    def content():
        yield "\ufeff"
        for row in export_rows(filters, is_demo=request.employee.is_demo, now=now, zone_name=zone_name):
            yield writer.writerow(row)

    filename = f"training-status-{now.astimezone(ZoneInfo(zone_name)):%Y%m%d-%H%M}.csv"
    response = StreamingHttpResponse(content(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store"
    return response


def _detail_context(assignment):
    from .learning_content import units
    slides = units(assignment.version)
    progress = ({entry.document_id: entry for entry in assignment.document_progress.all()}
                if assignment.version.content_format == "documents" else
                {entry.slide_id: entry for entry in assignment.slide_progress.all()})
    quiz = Quiz.objects.filter(version=assignment.version).first()
    attempts = [
        {"attempt": attempt, "score": displayed_score(attempt),
         "answers": submitted_answer_rows(attempt)}
        for attempt in assignment.quiz_attempts.order_by("submitted_at")
    ]
    now = timezone.now()
    status = assignment_status(assignment, now=now)
    summary = follow_up_summaries([assignment], now=now)[assignment.id]
    return {
        "assignment": assignment,
        "state": status.state_label,
        "archived": bool(assignment.archived_at),
        "is_pending": status.state == "pending",
        "progress": status.progress_label,
        "deadline": status.deadline_label,
        "slide_rows": [{"slide": slide, "entry": progress.get(slide.id)} for slide in slides],
        "quiz": quiz, "attempts": attempts,
        "history": assignment.history.select_related("actor").all(),
        "notifications": summary.notifications,
        "upcoming_reminders": summary.upcoming_reminders,
        "manual_next_at": summary.manual_next_at,
        "manual_can_send": summary.manual_can_send,
        "org_time_zone": settings.ORG_TIME_ZONE,
    }


def _render_detail(request, assignment_id, *, error=None, status=200):
    assignment = get_object_or_404(
        Assignment.objects.select_related("employee", "version"),
        pk=assignment_id, employee__is_demo=request.employee.is_demo,
    )
    return render(request, "training/assignment_admin_detail.html", {
        **_detail_context(assignment), "error": error,
        "back_url": safe_return_to(request, "assignment_dashboard", report_details=True),
    }, status=status)


@require_GET
@admin_required
def assignment_admin_detail(request, assignment_id):
    return _render_detail(request, assignment_id)


_ACTION_ERROR_STATUS = ((AssignmentInputError, 400), (AssignmentConflict, 409), (AssignmentAccessError, 403))


def _run_assignment_action(request, assignment_id, action):
    get_object_or_404(Assignment, pk=assignment_id, employee__is_demo=request.employee.is_demo)
    try:
        action()
    except (AssignmentInputError, AssignmentConflict, AssignmentAccessError) as exc:
        status = next(code for error_type, code in _ACTION_ERROR_STATUS if isinstance(exc, error_type))
        return _render_detail(request, assignment_id, error=str(exc), status=status)
    return redirect("assignment_admin_detail", assignment_id=assignment_id)


@require_POST
@admin_required
def change_deadline_view(request, assignment_id):
    return _run_assignment_action(request, assignment_id, lambda: change_assignment_deadline(
        assignment_id=assignment_id, actor=request.employee,
        due_date_value=request.POST.get("due_date", ""),
    ))


@require_POST
@admin_required
def cancel_assignment_view(request, assignment_id):
    return _run_assignment_action(request, assignment_id, lambda: cancel_assignment(
        assignment_id=assignment_id, actor=request.employee,
        reason=request.POST.get("reason", ""),
    ))


@require_POST
@admin_required
def waive_assignment_view(request, assignment_id):
    return _run_assignment_action(request, assignment_id, lambda: waive_assignment(
        assignment_id=assignment_id, actor=request.employee,
        reason=request.POST.get("reason", ""),
    ))


@require_POST
@admin_required
def archive_assignment_view(request, assignment_id):
    return _run_assignment_action(request, assignment_id, lambda: archive_assignment(
        assignment_id=assignment_id, actor=request.employee,
        reason=request.POST.get("reason", ""),
    ))


@require_POST
@admin_required
def restore_assignment_view(request, assignment_id):
    return _run_assignment_action(request, assignment_id, lambda: restore_assignment(
        assignment_id=assignment_id, actor=request.employee,
    ))


@require_POST
@admin_required
def send_manual_nudge_view(request, assignment_id):
    get_object_or_404(Assignment, pk=assignment_id, employee__is_demo=request.employee.is_demo)
    try:
        notification = request_manual_nudge(assignment_id=assignment_id, actor=request.employee)
    except ManualNudgeRateLimited as exc:
        return _render_detail(request, assignment_id, error=str(exc), status=429)
    except ManualNudgeIneligible as exc:
        return _render_detail(request, assignment_id, error=str(exc), status=409)
    return redirect("notification_detail", notification_id=notification.id)
