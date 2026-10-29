"""Completion cohorts shared by report pages, drilldowns, and CSV exports."""
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

from django.db.models import Case, When, Count, ExpressionWrapper, F, FloatField, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Lower, NullIf

from .assignment_reports import ReportFilterError, ReportFilters, report_assignments
from .models import AssignmentDocumentProgress, OutlineItem, AssignmentSlideProgress, Employee, Slide, Training, TrainingVersion

MODES = (("eligible", "Pending and completed"), ("canceled", "Canceled history"),
         ("waived", "Waived history"), ("archived", "Archived history"))
ACTIVITIES = (("active", "Active employees"), ("inactive", "Inactive employees"), ("all", "All employees"))
SORTS = (("title", "Name A–Z"), ("-title", "Name Z–A"), ("-total", "Most assignment cycles"),
         ("-rate", "Highest completion"), ("rate", "Lowest completion"), ("-overdue", "Most overdue"))


@dataclass(frozen=True)
class CompletionRequest:
    view: str = "training"
    mode: str = "eligible"
    activity: str = "active"
    q: str = ""
    sort: str = "title"
    version: uuid.UUID | None = None
    tab: str = "employee"
    slide: uuid.UUID | None = None
    document: uuid.UUID | None = None

    @classmethod
    def parse(cls, params, *, training=None, employee=None):
        values = {key: params.get(key, default) for key, default in
                  (("view", "training"), ("mode", "eligible"), ("activity", "active"),
                   ("sort", "title"), ("tab", "employee"))}
        if employee is not None and "activity" not in params and not employee.is_active:
            values["activity"] = "inactive"
        for key, allowed in (("view", {"training", "employee"}), ("mode", dict(MODES)),
                             ("activity", dict(ACTIVITIES)), ("sort", dict(SORTS)),
                             ("tab", {"employee", "slide", "document"})):
            if values[key] not in allowed:
                raise ReportFilterError(f"Choose a valid report {key}.")
        for key in ("version", "slide", "document"):
            try:
                values[key] = uuid.UUID(str(params[key])) if params.get(key) else None
            except (ValueError, TypeError, AttributeError):
                raise ReportFilterError(f"Choose a valid report {key}.") from None
        if values["version"] and (training is None or not TrainingVersion.objects.filter(
                pk=values["version"], training=training, status="published").exists()):
            raise ReportFilterError("Choose a published version of this training.")
        if values["tab"] in {"slide", "document"} and training is None:
            raise ReportFilterError("The slide view belongs to a training report.")
        if values["slide"] and (not values["version"] or values["tab"] != "employee" or not Slide.objects.filter(
                pk=values["slide"], version_id=values["version"]).exists()):
            raise ReportFilterError("Choose a slide from the selected version in By employee.")
        if values["document"] and (not values["version"] or values["tab"] != "employee" or not OutlineItem.objects.filter(
                pk=values["document"], version_id=values["version"], kind="document").exists()):
            raise ReportFilterError("Choose a document from the selected version in By employee.")
        if values["slide"] and values["document"]:
            raise ReportFilterError("Choose one content drilldown.")
        if values["version"] and values["tab"] in {"slide", "document"}:
            expected = "documents" if values["tab"] == "document" else "slides"
            if not TrainingVersion.objects.filter(pk=values["version"], content_format=expected).exists():
                raise ReportFilterError("Choose a published version with matching content format.")
        return cls(**values, q=" ".join(params.get("q", "").split())[:200])

    def params(self, **changes):
        params = {"view": self.view, "mode": self.mode, "activity": self.activity,
                  "q": self.q, "sort": self.sort, "tab": self.tab,
                  "version": str(self.version) if self.version else "",
                  "slide": str(self.slide) if self.slide else "",
                  "document": str(self.document) if self.document else ""}
        return {**params, **changes}

    def query(self, **changes):
        return urlencode(self.params(**changes))


def population(scope, *, now, training=None, employee=None):
    filters = ReportFilters(state="all" if scope.mode == "eligible" else scope.mode,
                            activity=scope.activity, q=scope.q,
                            training_id=training.pk if training else None,
                            employee_id=employee.pk if employee else None)
    assignments = report_assignments(filters, now=now)
    if scope.mode == "eligible":
        assignments = assignments.filter(Q(ending_value__isnull=True) | Q(ending_value="completed"))
    if scope.version:
        assignments = assignments.filter(version_id=scope.version)
    if scope.slide:
        assignments = assignments.filter(slide_progress__slide_id=scope.slide)
    if scope.document:
        assignments = assignments.filter(document_progress__document_id=scope.document)
    return assignments


def count_expressions():
    return {"total": Count("pk"), "completed": Count("pk", filter=Q(status_state="completed")),
            "pending": Count("pk", filter=Q(status_state="pending")),
            "overdue": Count("pk", filter=Q(status_state="pending", status_deadline="overdue")),
            "employees": Count("employee_id", distinct=True)}


def rate(completed, pending, *, eligible=True):
    return 100 * completed / (completed + pending) if eligible and completed + pending else None


def totals(assignments, scope):
    result = assignments.aggregate(**count_expressions())
    result["rate"] = rate(result["completed"], result["pending"], eligible=scope.mode == "eligible")
    return result


def summaries(assignments, scope):
    """Correlated aggregates keep evidence joins out of counts and retain empty entities."""
    if scope.view == "training":
        entities = Training.objects.with_display_title().filter(versions__status="published").distinct()
        grouping, title = "training_id", "display_title"
    else:
        entities = Employee.objects.filter(is_demo=True)
        if scope.activity != "all":
            entities = entities.filter(is_active=scope.activity == "active")
        grouping, title = "employee_id", "name"
    counts = assignments.order_by().filter(**{grouping: OuterRef("pk")}).values(grouping).annotate(
        **count_expressions())
    entities = entities.annotate(**{
        key: Coalesce(Subquery(counts.values(key)[:1], output_field=IntegerField()), Value(0))
        for key in count_expressions()
    })
    if scope.q:
        entities = entities.filter(total__gt=0)
    entities = entities.annotate(rate=ExpressionWrapper(
        100.0 * F("completed") / NullIf(F("completed") + F("pending"), 0), output_field=FloatField(),
    ) if scope.mode == "eligible" else Value(None, output_field=FloatField()))
    sort = scope.sort
    expression = Lower(title) if sort.lstrip("-") == "title" else F(sort.lstrip("-"))
    expression = expression.desc(nulls_last=True) if sort.startswith("-") else expression.asc(nulls_last=True)
    return entities.order_by(expression, "pk")


def evidence(assignments):
    acknowledgments = AssignmentSlideProgress.objects.filter(assignment_id=OuterRef("pk")).order_by().values(
        "assignment_id").annotate(n=Count("pk"))
    slides = Slide.objects.filter(version_id=OuterRef("version_id")).order_by().values("version_id").annotate(n=Count("pk"))
    docs = OutlineItem.objects.filter(version_id=OuterRef("version_id"), kind="document").order_by().values("version_id").annotate(n=Count("pk"))
    doc_acks = AssignmentDocumentProgress.objects.filter(assignment_id=OuterRef("pk")).order_by().values("assignment_id").annotate(n=Count("pk"))
    return assignments.annotate(
        acknowledged=Case(When(version__content_format="documents", then=Coalesce(Subquery(doc_acks.values("n")[:1]), Value(0))),
                          default=Coalesce(Subquery(acknowledgments.values("n")[:1]), Value(0))),
        slide_total=Case(When(version__content_format="documents", then=Coalesce(Subquery(docs.values("n")[:1]), Value(0))),
                         default=Coalesce(Subquery(slides.values("n")[:1]), Value(0))),
    )



def slide_summaries(assignments, version_id):
    total = assignments.count()
    counts = dict(AssignmentSlideProgress.objects.filter(
        assignment_id__in=assignments.order_by().values("pk"), slide__version_id=version_id,
    ).values("slide_id").annotate(n=Count("assignment_id", distinct=True)).values_list("slide_id", "n"))
    return [{"slide": slide, "acknowledged": counts.get(slide.pk, 0), "total": total,
             "rate": 100 * counts.get(slide.pk, 0) / total if total else None}
            for slide in Slide.objects.filter(version_id=version_id).select_related("version")]


def document_summaries(assignments, version_id):
    from .documents import documents
    version = TrainingVersion.objects.get(pk=version_id, content_format="documents")
    total = assignments.count()
    counts = dict(AssignmentDocumentProgress.objects.filter(
        assignment_id__in=assignments.order_by().values("pk"), document__version_id=version_id,
    ).values("document_id").annotate(n=Count("assignment_id", distinct=True)).values_list("document_id", "n"))
    return [{"slide": item, "acknowledged": counts.get(item.pk, 0), "total": total,
             "rate": 100 * counts.get(item.pk, 0) / total if total else None} for item in documents(version)]
