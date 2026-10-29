import csv
from itertools import islice
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .assignment_reports import ReportFilterError, _spreadsheet_safe, report_rows
from .browsing import browse
from .completion_reports import (ACTIVITIES, MODES, SORTS, CompletionRequest, evidence,
                                 population, slide_summaries, document_summaries, summaries, totals)
from .dev_auth import admin_required
from .models import Employee, Training


def _report(request, *, training_id=None, employee_id=None, export=False):
    training = get_object_or_404(Training.objects.with_display_title(), pk=training_id) if training_id else None
    employee = get_object_or_404(Employee, pk=employee_id) if employee_id else None
    now = timezone.now()
    try:
        scope = CompletionRequest.parse(request.GET, training=training, employee=employee)
    except ReportFilterError as exc:
        return render(request, "training/reports.html", {"error": str(exc)}, status=400)
    assignments = population(scope, is_demo=request.employee.is_demo, now=now, training=training, employee=employee)
    summary = totals(assignments, scope)
    detail = training is not None or employee is not None
    data = None
    if training and scope.tab in {"slide", "document"}:
        data = (document_summaries if scope.tab == "document" else slide_summaries)(assignments, scope.version) if scope.version else []
        label = "documents" if scope.tab == "document" else "slides"
        export_label = f"Export {label.rstrip('s')} acknowledgments CSV"
    elif detail:
        data = evidence(assignments)
        label, export_label = "assignments", "Export assignments CSV"
    else:
        data = summaries(assignments, scope, is_demo=request.employee.is_demo)
        label = "trainings" if scope.view == "training" else "employees"
        export_label = f"Export {scope.view} summary CSV"
    if export:
        if training and scope.tab in {"slide", "document"} and not scope.version:
            return HttpResponse("Choose a published version before exporting slides.", status=400)
        return _csv(data, scope, now=now, training=training, employee=employee)
    listing = browse(request, data, search_fields=(), extra_params=scope.params())
    if detail and not (training and scope.tab in {"slide", "document"}):
        rows = report_rows(listing.page.object_list, now=now)
    else:
        rows = listing.page.object_list
    overview_params = scope.query(version="", slide="", document="", tab="employee")
    export_name = "training_report_export" if training else "employee_report_export" if employee else "reports_export"
    export_url = reverse(export_name, args=[(training or employee).pk] if detail else [])
    context = {
        "scope": scope, "summary": summary, "rows": rows, "browse": listing,
        "training": training, "employee": employee, "detail": detail,
        "modes": MODES, "activities": ACTIVITIES, "sorts": SORTS,
        "export_url": f"{export_url}?{scope.query()}", "export_label": export_label,
        "overview_url": f"{reverse('reports')}?{overview_params}",
        "training_tab": "?" + scope.query(view="training", tab="employee", version="", slide="", document=""),
        "employee_tab": "?" + scope.query(view="employee", tab="employee", version="", slide="", document=""),
        "by_employee_url": "?" + scope.query(tab="employee", slide="", document=""),
        "by_slide_url": "?" + scope.query(tab="slide", slide="", document="", version=""),
        "by_document_url": "?" + scope.query(tab="document", slide="", document="", version=""),
        "content_label": "Document" if scope.tab == "document" else "Slide",
        "browse_label": label, "org_time_zone": settings.ORG_TIME_ZONE,
        "detail_query": urlencode({key: val for key, val in scope.params().items()
                                   if key not in {"version", "slide", "document", "tab"}}),
    }
    if training:
        context["versions"] = training.versions.filter(status="published").order_by("-version_number")
        if scope.tab in {"slide", "document"}:
            context["versions"] = context["versions"].filter(content_format="documents" if scope.tab == "document" else "slides")
        if scope.slide:
            context["selected_slide"] = training.versions.get(pk=scope.version).slides.get(pk=scope.slide)
        if scope.document:
            from .models import OutlineItem
            context["selected_document"] = OutlineItem.objects.get(pk=scope.document)
        if scope.tab in {"slide", "document"}:
            for row in rows:
                row["drill_url"] = "?" + scope.query(tab="employee", **({"document": str(row["slide"].pk), "slide": ""} if scope.tab == "document" else {"slide": str(row["slide"].pk), "document": ""}))
                row["all_url"] = "?" + scope.query(tab="employee", slide="", document="")
    if employee:
        context["groups"] = employee.group_memberships.select_related("group").order_by("group__name")
    with timezone.override(ZoneInfo(settings.ORG_TIME_ZONE)):
        return render(request, "training/reports.html", context)


def _csv(data, scope, *, now, training, employee):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="completion-report.csv"'
    writer = csv.writer(response)
    zone = ZoneInfo(settings.ORG_TIME_ZONE)
    prefix = [now.astimezone(zone).isoformat(), settings.ORG_TIME_ZONE, scope.mode, scope.activity, scope.q]
    base_columns = ["Generated at", "Time zone", "Population", "Employee activity", "Search"]

    def write(cells):
        writer.writerow([_spreadsheet_safe(value) for value in cells])

    if training and scope.tab in {"slide", "document"}:
        unit = "Document" if scope.tab == "document" else "Slide"
        write(base_columns + ["Training ID", "Training", "Version ID", "Version", unit + " ID", "Position",
                              unit, "Acknowledged cycles", "Assignment cycles", "Acknowledgment percent"])
        for row in data:
            slide = row["slide"]
            write(prefix + [training.pk, training.display_title, slide.version_id, slide.version.version_number,
                            slide.pk, slide.position, getattr(slide, "path", slide.header), row["acknowledged"], row["total"],
                            "" if row["rate"] is None else round(row["rate"], 1)])
    elif training or employee:
        write(base_columns + ["Employee ID", "Employee", "Employee email", "Training ID", "Assigned training title",
                              "Version ID", "Version", "Cycle", "State", "Deadline status", "Slides acknowledged",
                              "Slides total", "Latest quiz score", "Due date", "Completed at", "Assignment ID", "Content format", "Documents acknowledged", "Documents total"])
        for row in _assignment_export_rows(data, now=now):
            a = row.assignment
            write(prefix + [a.employee_id, a.employee.name, a.employee.email, a.training_id, a.version.title,
                            a.version_id, a.version.version_number, a.cycle_number, row.state, row.deadline,
                            a.acknowledged if a.version.content_format == "slides" else "", a.slide_total if a.version.content_format == "slides" else "", row.latest_score, a.due_date,
                            a.completed_at.astimezone(zone).isoformat() if a.completed_at else "", a.pk, a.version.content_format,
                            a.acknowledged if a.version.content_format == "documents" else "",
                            a.slide_total if a.version.content_format == "documents" else ""])
    else:
        write(base_columns + ["ID", "Training" if scope.view == "training" else "Employee", "Employee email",
                              "Employees", "Assignment cycles", "Completed cycles", "Pending cycles",
                              "Assignment completion percent", "Pending overdue"])
        for item in data.iterator(chunk_size=500):
            write(prefix + [item.pk, item.display_title if scope.view == "training" else item.name,
                            "" if scope.view == "training" else item.email, item.employees,
                            item.total, item.completed, item.pending,
                            "" if item.rate is None else round(item.rate, 1), item.overdue])
    return response


def _assignment_export_rows(data, *, now):
    assignments = data.iterator(chunk_size=500)
    while batch := list(islice(assignments, 500)):
        yield from report_rows(batch, now=now)


@require_GET
@admin_required
def reports(request):
    return _report(request)


@require_GET
@admin_required
def reports_export(request):
    return _report(request, export=True)


@require_GET
@admin_required
def training_report(request, training_id):
    return _report(request, training_id=training_id)


@require_GET
@admin_required
def training_report_export(request, training_id):
    return _report(request, training_id=training_id, export=True)


@require_GET
@admin_required
def employee_report(request, employee_id):
    return _report(request, employee_id=employee_id)


@require_GET
@admin_required
def employee_report_export(request, employee_id):
    return _report(request, employee_id=employee_id, export=True)
