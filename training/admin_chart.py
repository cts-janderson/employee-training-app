"""Build the administrator's pending-assignment overview from report records."""

from collections import defaultdict
from math import floor, log10

from django.db.models import Count

from .models import UNTITLED_TRAINING


SEGMENTS = (
    ("overdue_not_started", "Overdue · not started", "#0B4F6C", "#FFFFFF"),
    ("overdue_in_progress", "Overdue · in progress", "#2F7FA6", "#FFFFFF"),
    ("on_time_not_started", "On time · not started", "#5FAED3", "#0A2F40"),
    ("on_time_in_progress", "On time · in progress", "#9DD1E8", "#0A2F40"),
)
PLOT_X = 196
PLOT_WIDTH = 574
MAX_ROWS = 8


def _tick_step(maximum):
    if maximum <= 4:
        return 1
    rough = maximum / 4
    base = 10 ** floor(log10(rough))
    return next(multiplier * base for multiplier in (1, 2, 5, 10) if multiplier * base >= rough)


def pending_load_chart(assignments, training_labels):
    """Summarize every filtered pending assignment, including records beyond page one."""
    by_training = defaultdict(lambda: {key: 0 for key, *_ in SEGMENTS})
    counts = assignments.order_by().values(
        "training_id", "status_deadline", "status_progress",
    ).annotate(count=Count("id"))
    for cell in counts:
        key = f"{cell['status_deadline']}_{cell['status_progress']}"
        parts = by_training[cell["training_id"]]
        if key not in parts:
            raise ValueError(f"Unexpected pending assignment report group: {key}")
        parts[key] += cell["count"]

    if not by_training:
        return None

    rows = []
    for training_id, parts in by_training.items():
        name = training_labels.get(training_id, UNTITLED_TRAINING)
        total = sum(parts.values())
        rows.append({"id": training_id, "name": name, "parts": parts, "total": total,
                     "overdue": parts["overdue_not_started"] + parts["overdue_in_progress"]})
    rows.sort(key=lambda row: (-row["total"], -row["overdue"], row["name"].casefold(), str(row["id"])))

    leader_total = rows[0]["total"]
    total_assignments = sum(row["total"] for row in rows)
    total_overdue = sum(row["overdue"] for row in rows)
    displayed = rows[:MAX_ROWS]
    for index, row in enumerate(displayed):
        row["short_name"] = row["name"] if len(row["name"]) <= 20 else row["name"][:19] + "…"
        row["bar_y"] = 134 + index * 44
        row["label_y"] = row["bar_y"] + 19
        row["bar_width"] = round(PLOT_WIDTH * row["total"] / leader_total, 2)
        row["end_label"] = str(row["total"])
        row["end_x"] = round(PLOT_X + row["bar_width"] + 12, 2)
        current_x = PLOT_X
        row["segments"] = []
        for key, label, color, text_color in SEGMENTS:
            value = row["parts"][key]
            if not value:
                continue
            width = PLOT_WIDTH * value / leader_total
            row["segments"].append({"label": label, "value": value, "x": round(current_x, 2),
                                    "width": round(width, 2), "text_x": round(current_x + width / 2, 2),
                                    "text": str(value) if width >= 18 else "",
                                    "color": color, "text_color": text_color})
            current_x += width

    axis_y = 134 + len(displayed) * 44 + 16
    step = _tick_step(leader_total)
    values = list(range(0, leader_total + 1, step))
    if values[-1] != leader_total:
        values.append(leader_total)
    ticks = [{"value": value, "x": round(PLOT_X + PLOT_WIDTH * value / leader_total, 2)}
             for value in values]
    chart_height = axis_y + 28
    title = "Pending assignments by training"
    return {
        "title": title, "rows": displayed,
        "total_rows": len(rows), "total_assignments": total_assignments, "total_overdue": total_overdue,
        "legend": [{"label": label, "color": color, "x": 196 + index * 183}
                   for index, (_, label, color, _) in enumerate(SEGMENTS)],
        "ticks": ticks, "grid_bottom": axis_y - 11, "axis_y": axis_y,
        "height": chart_height,
    }
