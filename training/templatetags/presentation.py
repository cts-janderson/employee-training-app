"""Product labels for operational records; stored audit values stay unchanged."""
from django import template

register = template.Library()


@register.filter
def notification_status(record):
    return "Processed" if record.status == "sent" else record.get_status_display()


@register.filter
def delivery_outcome(record):
    return {"accepted": "Processed", "transient_failure": "Will retry",
            "permanent_failure": "Could not send", "ambiguous": "Delivery unconfirmed"}.get(
                record.outcome, record.get_outcome_display())


@register.filter
def notification_reason(reason):
    labels = {
        "schedule_elapsed": "The scheduled time has passed.",
        "deadline_superseded": "The due date changed.",
        "reminder_expired": "The reminder is no longer needed.",
        "reminder_superseded": "A more recent reminder is available.",
        "employee_inactive": "This employee is inactive.",
        "employee_inactive_during_window": "The employee was inactive when the reminder was due.",
        "assignment_completed": "The assignment is complete.",
        "assignment_canceled": "The assignment was canceled.",
        "assignment_waived": "The assignment was waived.",
        "invalid_schedule": "The reminder could not be scheduled.",
    }
    return labels.get(reason, str(reason).replace("_", " ").capitalize())
