from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .dev_auth import demo_admin_required
from .groups import GroupInputError, create_group, group_members, rename_group, set_group_members
from .models import Employee, EmployeeGroup


@require_http_methods(["GET", "POST"])
@demo_admin_required
def group_list(request):
    error = None
    if request.method == "POST":
        try:
            group = create_group(name=request.POST.get("name", ""), actor=request.employee)
            return redirect("group_detail", group_id=group.id)
        except GroupInputError as exc:
            error = str(exc)
    groups = EmployeeGroup.objects.annotate(member_count=Count("memberships")).order_by("name", "id")
    return render(request, "training/groups.html", {"groups": groups, "error": error},
                  status=400 if error else 200)


@require_http_methods(["GET", "POST"])
@demo_admin_required
def group_detail(request, group_id):
    group = get_object_or_404(EmployeeGroup, pk=group_id)
    error = None
    if request.method == "POST":
        try:
            if request.POST.get("action") == "rename":
                rename_group(group_id=group.id, name=request.POST.get("name", ""), actor=request.employee)
            elif request.POST.get("action") == "members":
                set_group_members(group_id=group.id, employee_ids=request.POST.getlist("employee_id"),
                                  actor=request.employee)
            else:
                raise GroupInputError("Choose a valid group action.")
            return redirect("group_detail", group_id=group.id)
        except GroupInputError as exc:
            error = str(exc)
    members = {employee.id for employee in group_members(group)}
    choices = Employee.objects.filter(Q(is_demo=True, is_active=True) | Q(pk__in=members)).order_by("name", "id")
    return render(request, "training/group_detail.html", {
        "group": group, "choices": [{"employee": employee, "selected": employee.id in members}
                                     for employee in choices],
        "changes": group.changes.select_related("actor", "employee").order_by("-occurred_at", "-id"),
        "error": error,
    }, status=400 if error else 200)
