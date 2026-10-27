"""Summarize quiz outcomes for the administrator's assignment report."""

from collections import defaultdict

from .models import UNTITLED_TRAINING
from .quizzes import displayed_score, quiz_record


MAX_ROWS = 8
PLOT_X = 248
PLOT_WIDTH = 490
OUTCOMES = (
    ("passed", "Passed", "#16A34A"),
    ("not_attempted", "Not attempted", "#F59E0B"),
    ("retry_needed", "Latest attempt failed · retry needed", "#DC2626"),
)
OUTCOME_LABELS = {key: label for key, label, _ in OUTCOMES}


def _whole_percentages(parts, total):
    portions = {key: divmod(parts[key] * 100, total) for key, _, _ in OUTCOMES}
    percentages = {key: portion[0] for key, portion in portions.items()}
    remaining = 100 - sum(percentages.values())
    order = sorted((key for key, _, _ in OUTCOMES),
                   key=lambda key: -portions[key][1])
    for key in order[:remaining]:
        percentages[key] += 1
    return percentages


def quiz_outcomes_chart(assignments, training_labels):
    """Count each matching quiz assignment once and retain its employee detail."""
    groups = defaultdict(lambda: {"passed": 0, "retry_needed": 0, "not_attempted": 0,
                                  "people": []})
    for assignment in assignments.iterator(chunk_size=500):
        record = quiz_record(assignment)
        latest, outcome = record.latest, record.outcome
        group = groups[assignment.training_id]
        group[outcome] += 1
        group["people"].append({
            "assignment": assignment,
            "outcome": outcome,
            "outcome_label": OUTCOME_LABELS[outcome],
            "latest_score": f"{displayed_score(latest)}%" if latest else "—",
            "latest_at": latest.submitted_at if latest else None,
        })

    if not groups:
        return None

    rows = []
    for training_id, group in groups.items():
        total = sum(group[key] for key, _, _ in OUTCOMES)
        group["people"].sort(key=lambda person: (
            person["assignment"].employee.name.casefold(),
            person["assignment"].cycle_number,
            person["assignment"].assigned_at,
            str(person["assignment"].id),
        ))
        rows.append({"id": training_id, "name": training_labels.get(training_id, UNTITLED_TRAINING),
                     "parts": {key: group[key] for key, _, _ in OUTCOMES},
                     "people": group["people"], "total": total,
                     "retry_rate": group["retry_needed"] / total})
    rows.sort(key=lambda row: (-row["parts"]["retry_needed"], -row["retry_rate"],
                               row["name"].casefold(), str(row["id"])))

    displayed = rows[:MAX_ROWS]
    for index, row in enumerate(displayed):
        row["short_name"] = row["name"] if len(row["name"]) <= 23 else row["name"][:22] + "…"
        row["bar_y"] = 145 + index * 44
        row["label_y"] = row["bar_y"] + 18
        row["segments"] = []
        percentages = _whole_percentages(row["parts"], row["total"])
        current_x = PLOT_X
        for key, label, color in OUTCOMES:
            value = row["parts"][key]
            if not value:
                continue
            width = PLOT_WIDTH * value / row["total"]
            row["segments"].append({
                "key": key, "label": label, "value": value, "color": color,
                "x": round(current_x, 2), "width": round(width, 2),
                "text_x": round(current_x + width / 2, 2),
                "text": f"{percentages[key]}%" if width >= 50 else "",
            })
            current_x += width

    total_assignments = sum(row["total"] for row in rows)
    return {
        "title": "Quiz outcomes by training", "rows": displayed, "all_rows": rows,
        "total_rows": len(rows), "total_assignments": total_assignments,
        "legend": [{"key": key, "label": label, "color": color}
                              for key, label, color in OUTCOMES],
        "height": 145 + len(displayed) * 44 + 14,
    }
