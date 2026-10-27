"""Regenerate the administrator chart preview with disposable synthetic records."""

import os
from datetime import timedelta
from pathlib import Path
from xml.etree import ElementTree

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["DJANGO_DEBUG"] = "1"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402
from django.template.loader import render_to_string  # noqa: E402
from django.utils import timezone  # noqa: E402

from training.admin_chart import pending_load_chart  # noqa: E402
from training.assignment_reports import ReportFilters, report_assignments  # noqa: E402
from training.models import Assignment, Employee, Training, TrainingVersion  # noqa: E402


SAMPLES = (
    ("Workplace safety", (3, 2, 4, 3)),
    ("Data privacy", (2, 1, 3, 3)),
    ("Code of conduct", (1, 1, 3, 3)),
    ("Equipment care", (2, 1, 2, 2)),
    ("Incident response", (1, 1, 2, 2)),
    ("Quality checks", (1, 0, 2, 2)),
    ("Accessibility", (0, 1, 1, 2)),
    ("New hire essentials", (0, 0, 1, 2)),
)


def main():
    call_command("migrate", verbosity=0)
    now = timezone.now()
    labels = {}
    for training_index, (title, parts) in enumerate(SAMPLES):
        training = Training.objects.create()
        version = TrainingVersion.objects.create(
            training=training, version_number=1, title=title,
            status=TrainingVersion.Status.PUBLISHED,
        )
        labels[training.id] = title
        for bucket, count in enumerate(parts):
            for item_index in range(count):
                employee = Employee.objects.create(
                    name=f"Learner {training_index}-{bucket}-{item_index}",
                    email=f"learner{training_index}-{bucket}-{item_index}@example.test",
                    is_demo=True,
                )
                overdue = bucket < 2
                due_at = now - timedelta(days=1) if overdue else now + timedelta(days=7)
                Assignment.objects.create(
                    employee=employee, training=training, version=version,
                    due_date=due_at.date(), due_timezone="UTC", due_at=due_at,
                    started_at=now - timedelta(days=2) if bucket in (1, 3) else None,
                )
    chart = pending_load_chart(report_assignments(ReportFilters(), now=now), labels)
    html = render_to_string("training/_assignment_relay_chart.html", {"chart": chart})
    svg = html[html.index("<svg"):html.index("</svg>") + 6]
    ElementTree.fromstring(svg)
    output = Path(__file__).resolve().parents[1] / "docs/design/admin-tracking-relay-preview.svg"
    output.write_text(svg)
    print(f"Wrote {output} with {chart['total_assignments']} synthetic assignments")


if __name__ == "__main__":
    main()
