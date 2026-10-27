import uuid
from zoneinfo import ZoneInfo

from django.conf import settings
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .assignments import (
    AssignmentAccessError,
    AssignmentInputError,
    LearnerAccessLevel,
    Recipients,
    assignment_status,
    create_assignments,
    validate_new_due_date,
    learner_next_step,
    learner_access,
    parse_recipients_request,
    plan_assignments,
    visit_slide,
    acknowledge_slide,
)
from .dev_auth import demo_actor_required, demo_admin_required
from .models import Assignment, AssignmentOperationResult, Employee, EmployeeGroup, Quiz, Slide, Training, TrainingVersion, UNTITLED_TRAINING
from .quizzes import displayed_score
from .renewals import set_renewal_interval



def _learner_next_action(assignment, slides, acknowledged_ids, has_quiz):
    step, target = learner_next_step(assignment, slides, acknowledged_ids, has_quiz)
    if step == "review":
        return {"label": "Review", "url": reverse("assignment_detail", args=[assignment.id])}
    if step in {"start", "resume"}:
        return {
            "label": "Resume" if step == "resume" else "Start",
            "url": reverse("assignment_slide", args=[assignment.id, target.id]),
            "target": target,
        }
    return {"label": "Take quiz", "url": reverse("take_quiz", args=[assignment.id])}


@require_http_methods(["GET", "POST"])
@demo_admin_required
def assign_training(request, training_id):
    training = get_object_or_404(Training, pk=training_id)
    versions = list(training.versions.filter(status=TrainingVersion.Status.PUBLISHED).order_by("-version_number"))
    employees = Employee.objects.filter(is_demo=True).order_by("name")
    groups = EmployeeGroup.objects.order_by("name", "id")
    error = None
    results = None
    review = None
    operation_id = str(uuid.uuid4())
    selected_version_id = str(versions[0].id) if versions else ""
    requested_version_id = request.GET.get("version_id", "")
    if request.method == "GET" and requested_version_id in {str(version.id) for version in versions}:
        selected_version_id = requested_version_id
    selected_due_date = ""
    selected_employee_ids = set()
    selected_source = "employees"
    selected_group_id = ""
    if request.method == "POST":
        selected_version_id = request.POST.get("version_id", "")
        selected_due_date = request.POST.get("due_date", "")
        submitted_employee_ids = request.POST.getlist("employee_id")
        selected_employee_ids = set(submitted_employee_ids)
        selected_source = request.POST.get("recipient_source", "employees")
        selected_group_id = request.POST.get("group_id", "")
        operation_id = request.POST.get("operation_id", "")
        action = request.POST.get("action", "submit")
        try:
            if selected_version_id not in {str(version.id) for version in versions}:
                raise AssignmentInputError("Choose a published version of this training.")
            if selected_source == "group":
                recipients = Recipients.group(selected_group_id)
            elif selected_source == "employees":
                recipients = Recipients.employees(submitted_employee_ids)
            else:
                raise AssignmentInputError("Choose valid assignment recipients.")
            if action == "review":
                review = _assignment_review(
                    training, versions, selected_version_id, selected_due_date,
                    recipients, operation_id,
                )
            elif action == "edit":
                pass
            elif action == "submit":
                results = create_assignments(
                    operation_id=operation_id,
                    actor=request.employee,
                    version_id=selected_version_id,
                    recipients=recipients,
                    due_date_value=selected_due_date,
                )
                operation_id = str(uuid.uuid4())
                selected_due_date = ""
                selected_employee_ids = set()
                selected_group_id = ""
                selected_source = "employees"
            else:
                raise AssignmentInputError("Choose a valid assignment action.")
        except (KeyError, AssignmentInputError) as exc:
            error = str(exc)
    selected_version = next((version for version in versions if str(version.id) == selected_version_id), None)
    result_counts = {outcome: sum(result.outcome == outcome for result in results or [])
                     for outcome in ("created", "already_assigned", "conflict", "inactive")}
    return render(request, "training/assign.html", {
        "training": training,
        "training_title": selected_version.title if selected_version else (versions[0].title if versions else UNTITLED_TRAINING),
        "version_rows": [{"version": version, "selected": str(version.id) == selected_version_id} for version in versions],
        "employee_rows": [{"employee": employee, "selected": str(employee.id) in selected_employee_ids} for employee in employees],
        "group_rows": [{"group": group, "selected": str(group.id) == selected_group_id,
                        "member_count": group.memberships.count()} for group in groups],
        "selected_source": selected_source, "selected_group_id": selected_group_id,
        "selected_due_date": selected_due_date,
        "org_time_zone": settings.ORG_TIME_ZONE, "operation_id": operation_id,
        "results": results, "result_counts": result_counts, "review": review, "error": error,
    }, status=400 if error else 200)


def _assignment_review(training, versions, selected_version_id, due_date_value, recipients, operation_id):
    _, recipients = parse_recipients_request(operation_id, recipients)
    due_date, zone_name, due_at = validate_new_due_date(due_date_value)
    resolved = recipients.resolve()
    employees = [item.employee for item in resolved]
    version = next(version for version in versions if str(version.id) == selected_version_id)
    rows = []
    for employee, outcome, current in plan_assignments(version, employees, due_date=due_date,
                                                        zone_name=zone_name, due_at=due_at):
        if outcome == AssignmentOperationResult.Outcome.INACTIVE:
            status = "Inactive — no assignment will be created"
        elif outcome == AssignmentOperationResult.Outcome.ALREADY_ASSIGNED:
            status = "Already assigned — existing obligation will be reported"
        elif outcome == AssignmentOperationResult.Outcome.CONFLICT:
            status = f"Conflict — outstanding version {current.version.version_number} must be resolved"
        else:
            status = "Ready to assign"
        recipient = next(item for item in resolved if item.employee.id == employee.id)
        rows.append({"employee": employee, "status": status,
                     "renews": recipient.renews, "renewal_date": recipient.renewal_date})
    return {"version": version, "due_date": due_date, "zone_name": zone_name,
            "rows": rows, "count": len(rows), **recipients.review_summary()}


@require_POST
@demo_admin_required
def change_renewal_interval(request, training_id):
    training = get_object_or_404(Training, pk=training_id)
    try:
        set_renewal_interval(training_id=training.id, months=request.POST.get("months", ""), actor=request.employee)
    except ValueError as exc:
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest(str(exc))
    return redirect("draft_editor", training_id=training.id)


@require_http_methods(["GET", "POST"])
@demo_admin_required
def renew_training(request, training_id):
    training = get_object_or_404(Training, pk=training_id)
    versions = list(training.versions.filter(status=TrainingVersion.Status.PUBLISHED).order_by("-version_number"))
    selected_version_id = str(versions[0].id) if versions else ""
    as_of = timezone.now().astimezone(ZoneInfo(settings.ORG_TIME_ZONE)).date().isoformat()
    due_date = ""
    operation_id = str(uuid.uuid4())
    review = results = error = None
    if request.method == "POST":
        selected_version_id = request.POST.get("version_id", "")
        as_of = request.POST.get("as_of", "")
        due_date = request.POST.get("due_date", "")
        operation_id = request.POST.get("operation_id", "")
        action = request.POST.get("action", "review")
        try:
            if selected_version_id not in {str(version.id) for version in versions}:
                raise AssignmentInputError("Choose a published version of this training.")
            recipients = Recipients.renewal(training.id, as_of)
            if action == "review":
                review = _assignment_review(training, versions, selected_version_id, due_date,
                                            recipients, operation_id)
            elif action == "submit":
                results = create_assignments(operation_id=operation_id, actor=request.employee,
                                             version_id=selected_version_id, recipients=recipients,
                                             due_date_value=due_date)
                operation_id = str(uuid.uuid4())
            else:
                raise AssignmentInputError("Choose a valid assignment action.")
        except AssignmentInputError as exc:
            error = str(exc)
    return render(request, "training/renew.html", {
        "training": training, "versions": versions, "selected_version_id": selected_version_id,
        "as_of": as_of, "due_date": due_date, "operation_id": operation_id,
        "review": review, "results": results, "error": error,
        "org_time_zone": settings.ORG_TIME_ZONE,
    }, status=400 if error else 200)


@require_GET
@demo_actor_required
def my_training(request):
    assignments = list(Assignment.objects.visible_to_learner(request.employee).select_related(
        "version", "resume_slide",
    ).prefetch_related("version__slides", "slide_progress"))
    quiz_version_ids = set(Quiz.objects.filter(
        version_id__in=[assignment.version_id for assignment in assignments],
    ).values_list("version_id", flat=True))
    now = timezone.now()
    unfinished = []
    completed = []
    waived = []
    for assignment in assignments:
        slides = list(assignment.version.slides.all())
        acknowledged_ids = {item.slide_id for item in assignment.slide_progress.all()}
        status = assignment_status(assignment, now=now)
        row = {
            "assignment": assignment,
            "progress": status.progress_label,
            "deadline": status.deadline_label,
            "deadline_code": status.deadline,
            "acknowledged_count": len(acknowledged_ids),
            "slide_count": len(slides),
            "next_action": _learner_next_action(
                assignment, slides, acknowledged_ids, assignment.version_id in quiz_version_ids,
            ),
        }
        (waived if status.state == "waived" else completed if status.state == "completed" else unfinished).append(row)
    unfinished.sort(key=lambda row: (
        row["deadline_code"] != "overdue", row["assignment"].due_at, row["assignment"].assigned_at,
    ))
    completed.sort(key=lambda row: (row["assignment"].completed_at, row["assignment"].id), reverse=True)
    return render(request, "training/my_training.html", {"unfinished": unfinished, "completed": completed,
                                                          "waived": waived})


@require_GET
@demo_actor_required
def assignment_detail(request, assignment_id):
    access = learner_access(
        assignment_id, request.employee,
        queryset=Assignment.objects.select_related("version", "resume_slide"),
    )
    if not access.can_review:
        raise Http404("Assignment not found.")
    assignment = access.assignment
    slides = list(assignment.version.slides.all())
    acknowledged_ids = set(assignment.slide_progress.values_list("slide_id", flat=True))
    rows = [{"slide": slide, "acknowledged": slide.id in acknowledged_ids} for slide in slides]
    quiz = Quiz.objects.filter(version=assignment.version).first()
    attempts = [{"attempt": attempt, "score": displayed_score(attempt)}
                for attempt in assignment.quiz_attempts.order_by("-submitted_at")]
    next_action = _learner_next_action(assignment, slides, acknowledged_ids, quiz is not None)
    now = timezone.now()
    status = assignment_status(assignment, now=now)
    # The loaded slide and acknowledgment sets follow the same rule as slides_acknowledged().
    return render(request, "training/assignment_detail.html", {
        "assignment": assignment, "rows": rows,
        "quiz": quiz, "quiz_ready": len(acknowledged_ids) == len(slides), "attempts": attempts,
        "progress": status.progress_label,
        "deadline": status.deadline_label,
        "acknowledged_count": len(acknowledged_ids), "slide_count": len(slides),
        "remaining_count": len(slides) - len(acknowledged_ids), "next_action": next_action,
        "slide_acknowledged": request.GET.get("slide_acknowledged") == "1",
    })


@require_GET
@demo_actor_required
def assignment_slide(request, assignment_id, slide_id):
    access = learner_access(assignment_id, request.employee)
    if not access.can_review:
        raise Http404("Assignment not found.")
    assignment = access.assignment
    slide = get_object_or_404(Slide, pk=slide_id)
    if slide.version_id != assignment.version_id:
        return HttpResponseForbidden("This slide is not in the assigned version.")
    if access.can_act:
        try:
            assignment, slide = visit_slide(assignment_id=assignment.id, slide_id=slide.id, employee=request.employee)
        except AssignmentAccessError as exc:
            return HttpResponseForbidden(str(exc))
    slides = list(assignment.version.slides.all())
    index = next(index for index, item in enumerate(slides) if item.id == slide.id)
    acknowledged_ids = set(assignment.slide_progress.values_list("slide_id", flat=True))
    acknowledged = slide.id in acknowledged_ids
    return render(request, "training/assignment_slide.html", {
        "assignment": assignment, "slide": slide, "acknowledged": acknowledged,
        "previous": slides[index - 1] if index > 0 else None,
        "next": slides[index + 1] if index + 1 < len(slides) else None,
        "slide_count": len(slides), "remaining_count": len(slides) - len(acknowledged_ids),
        "previous_acknowledged": request.GET.get("slide_acknowledged") == "1",
    })


@require_POST
@demo_actor_required
def acknowledge(request, assignment_id, slide_id):
    access = learner_access(assignment_id, request.employee)
    if access.level == LearnerAccessLevel.REVIEW:
        return HttpResponseForbidden(access.message)
    if not access.can_act:
        raise Http404("Assignment not found.")
    assignment = access.assignment
    slide = get_object_or_404(Slide, pk=slide_id)
    try:
        assignment = acknowledge_slide(assignment_id=assignment.id, slide_id=slide.id, employee=request.employee)
    except AssignmentAccessError as exc:
        return HttpResponseForbidden(str(exc))
    if not assignment.completed_at:
        acknowledged_ids = set(assignment.slide_progress.values_list("slide_id", flat=True))
        next_action = _learner_next_action(
            assignment, list(assignment.version.slides.all()), acknowledged_ids,
            Quiz.objects.filter(version=assignment.version).exists(),
        )
        if next_action["label"] == "Resume":
            return redirect(next_action["url"] + "?slide_acknowledged=1")
    return redirect(reverse("assignment_detail", args=[assignment.id]) + "?slide_acknowledged=1")
