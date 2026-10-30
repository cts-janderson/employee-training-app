from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .browsing import browse
from .dev_auth import admin_required
from .models import Notification
from .notifications import NotificationError, reconcile_notification, retry_notification


DELIVERY_GUIDANCE = {
    Notification.Status.PENDING: ("Queued for delivery", "This message is waiting to be processed."),
    Notification.Status.SENDING: ("Sending", "This message is being processed."),
    Notification.Status.RETRY: ("Retry scheduled", "Another delivery attempt is scheduled."),
    Notification.Status.SENT: ("Processed", "Processing is complete. No action is needed."),
    Notification.Status.SUPPRESSED: ("Suppressed", "This message will not be sent. See the reason below."),
    Notification.Status.BLOCKED: ("Blocked", "Check the recipient and assignment before retrying."),
    Notification.Status.EXHAUSTED: ("Retries exhausted", "Check the delivery attempts before retrying."),
    Notification.Status.AMBIGUOUS: ("Ambiguous outcome", "Confirm whether the message was received before retrying."),
}


def _delivery_guidance(notification):
    if notification.status == Notification.Status.SENT:
        confirmed = any(entry.decision == "delivered" for entry in notification.reconciliations.all())
        accepted = any(attempt.outcome == "accepted" for attempt in notification.delivery_attempts.all())
        if confirmed and not accepted:
            return "Confirmed delivered", "Delivery was confirmed. No additional message was sent."
    return DELIVERY_GUIDANCE[notification.status]


def _status_tone(status):
    if status == Notification.Status.SENT:
        return "success"
    if status in {Notification.Status.RETRY, Notification.Status.BLOCKED,
                  Notification.Status.EXHAUSTED, Notification.Status.AMBIGUOUS}:
        return "attention"
    if status in {Notification.Status.PENDING, Notification.Status.SENDING}:
        return "queued"
    return "neutral"


@require_GET
@admin_required
def notification_log(request):
    status = request.GET.get("status", "all")
    if status not in {"all", *Notification.Status.values}:
        status = "all"
    kind = request.GET.get("kind", "all")
    if kind not in {"all", *Notification.Kind.values}:
        kind = "all"
    base = Notification.objects.filter(assignment__employee__is_demo=request.employee.is_demo)
    counts = base.aggregate(
        total=Count("id"),
        queued=Count("id", filter=Q(status__in=[Notification.Status.PENDING, Notification.Status.SENDING])),
        sent=Count("id", filter=Q(status=Notification.Status.SENT)),
        attention=Count("id", filter=Q(status__in=[Notification.Status.RETRY, Notification.Status.BLOCKED,
                                                    Notification.Status.EXHAUSTED, Notification.Status.AMBIGUOUS])),
    )
    notifications = base.select_related(
        "assignment__employee", "assignment__version", "requested_by",
    ).prefetch_related("reconciliations", "delivery_attempts").order_by("-created_at", "-pk")
    if status != "all":
        notifications = notifications.filter(status=status)
    if kind != "all":
        notifications = notifications.filter(kind=kind)
    # Paged before anything is evaluated, so guidance is built only for the rows on this page.
    listing = browse(request, notifications, extra_params={"status": status, "kind": kind},
                     search_fields=["assignment__employee__name", "assignment__version__title"])
    search = listing.query
    return render(request, "training/notification_log.html", {
        "browse": listing,
        "browse_label": "notifications",
        "notification_rows": [{"item": item, "outcome": _delivery_guidance(item)[0],
                               "tone": _status_tone(item.status)}
                              for item in listing.page],
        "counts": counts,
        "selected_status": status,
        "selected_kind": kind,
        "search": search,
        "status_choices": [(value, "Processed" if value == "sent" else label) for value, label in Notification.Status.choices],
        "kind_choices": Notification.Kind.choices,
    })


def _render_detail(request, notification_id, *, error=None, status=200):
    notification = get_object_or_404(
        Notification.objects.select_related("assignment__employee", "assignment__version", "requested_by").prefetch_related(
            "delivery_attempts", "reconciliations__actor",
        ), pk=notification_id, assignment__employee__is_demo=request.employee.is_demo,
    )
    return render(request, "training/notification_detail.html", {
        "notification": notification, "error": error,
        "outcome": _delivery_guidance(notification)[0],
        "next_step": _delivery_guidance(notification)[1],
    }, status=status)


@require_GET
@admin_required
def notification_detail(request, notification_id):
    return _render_detail(request, notification_id)


@require_POST
@admin_required
def retry_notification_view(request, notification_id):
    get_object_or_404(Notification, pk=notification_id, assignment__employee__is_demo=request.employee.is_demo)
    try:
        retry_notification(notification_id=notification_id, actor=request.employee)
    except NotificationError as exc:
        return _render_detail(request, notification_id, error=str(exc), status=400)
    return redirect("notification_detail", notification_id=notification_id)


@require_POST
@admin_required
def reconcile_notification_view(request, notification_id):
    get_object_or_404(Notification, pk=notification_id, assignment__employee__is_demo=request.employee.is_demo)
    try:
        reconcile_notification(
            notification_id=notification_id, actor=request.employee,
            decision=request.POST.get("decision", ""), reason=request.POST.get("reason", ""),
        )
    except NotificationError as exc:
        return _render_detail(request, notification_id, error=str(exc), status=400)
    return redirect("notification_detail", notification_id=notification_id)
