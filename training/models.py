import uuid

from django.contrib.auth.base_user import AbstractBaseUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Case, CharField, F, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce, Lower, NullIf
from django.utils import timezone
from django.utils.crypto import salted_hmac


class Employee(AbstractBaseUser):
    class Role(models.TextChoices):
        ADMINISTRATOR = "administrator", "Administrator"
        EMPLOYEE = "employee", "Employee"

    class Approval(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    email = models.EmailField()
    verified_email = models.EmailField(blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    is_active = models.BooleanField(default=True)
    is_demo = models.BooleanField(default=False)
    approval_status = models.CharField(max_length=10, choices=Approval.choices, default=Approval.APPROVED)
    approval_decided_at = models.DateTimeField(null=True, blank=True)
    approval_decided_by = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True,
                                            related_name="approval_decisions")
    # Bumped whenever a verification, setup or reset link is sent, so earlier links stop working.
    link_generation = models.PositiveIntegerField(default=0)
    # Bumped on deactivation, so sessions from before it stay dead after reactivation.
    session_version = models.PositiveIntegerField(default=0)
    # Set when the last verification, setup or reset email couldn't be sent; cleared by the next success.
    link_send_failed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Email uniqueness is the case-insensitive constraint below; see ADR-0009.
    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="employee_email_ci_unique")]

    def save(self, *args, **kwargs):
        # Demo and invited employees have no password until one is set through an account link.
        if not self.password:
            self.set_unusable_password()
        super().save(*args, **kwargs)

    @property
    def email_is_verified(self):
        return bool(self.verified_email) and self.verified_email.lower() == self.email.lower()

    def _get_session_auth_hash(self, secret=None):
        # Covers the current and fallback hashes, which Django derives from this method.
        key_salt = "django.contrib.auth.models.AbstractBaseUser.get_session_auth_hash"
        return salted_hmac(key_salt, f"{self.password}:{self.session_version}",
                           secret=secret, algorithm="sha256").hexdigest()

    def __str__(self):
        return self.name


UNTITLED_TRAINING = "Untitled training"


class TrainingQuerySet(models.QuerySet):
    def with_display_title(self):
        """Prefer the newest published title, then the draft, then the fallback."""
        published_title = TrainingVersion.objects.filter(
            training_id=OuterRef("pk"), status=TrainingVersion.Status.PUBLISHED,
        ).order_by("-version_number").values("title")[:1]
        draft_title = TrainingVersion.objects.filter(
            training_id=OuterRef("pk"), status=TrainingVersion.Status.DRAFT,
        ).values("title")[:1]
        chosen = Coalesce(Subquery(published_title), Subquery(draft_title), output_field=CharField())
        return self.annotate(display_title=Coalesce(
            NullIf(chosen, Value("")), Value(UNTITLED_TRAINING), output_field=CharField(),
        ))


class Training(models.Model):
    objects = TrainingQuerySet.as_manager()
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    renewal_interval_months = models.PositiveSmallIntegerField(null=True, blank=True)
    # Demo and real administrators keep separate libraries; see docs/plans/31-real-employee-workflows.md.
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=Q(renewal_interval_months__isnull=True)
            | Q(renewal_interval_months__gte=1, renewal_interval_months__lte=120),
            name="renewal_interval_months_1_to_120",
        )]


class TrainingVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    content_format = models.CharField(max_length=12, choices=[("slides", "Slides"), ("documents", "Documents")], default="slides")
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    training = models.ForeignKey(Training, on_delete=models.PROTECT, related_name="versions")
    version_number = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["training_id", "version_number"]
        constraints = [
            models.UniqueConstraint(fields=["training", "version_number"], name="unique_training_version_number"),
            models.UniqueConstraint(fields=["training"], condition=Q(status="draft"), name="one_draft_per_training"),
            models.CheckConstraint(condition=Q(version_number__gte=1), name="version_number_positive"),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, status=self.Status.PUBLISHED).exists():
            raise ValidationError("A published training version cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if type(self).objects.filter(pk=self.pk, status=self.Status.PUBLISHED).exists():
            raise ValidationError("A published training version cannot be deleted.")
        return super().delete(*args, **kwargs)


class VersionContent(models.Model):
    published_version_lookup = "version"
    published_change_message = "Published version content cannot be changed."
    published_delete_message = "Published version content cannot be deleted."

    class Meta:
        abstract = True

    def _current_version_id(self):
        owner = self
        path = self.published_version_lookup.split("__")
        for part in path[:-1]:
            owner = getattr(owner, part)
        return getattr(owner, f"{path[-1]}_id")

    def _published_old_or_current(self):
        lookup = f"{self.published_version_lookup}__status"
        old_published = self.pk and type(self).objects.filter(
            pk=self.pk, **{lookup: TrainingVersion.Status.PUBLISHED},
        ).exists()
        new_published = TrainingVersion.objects.filter(
            pk=self._current_version_id(), status=TrainingVersion.Status.PUBLISHED,
        ).exists()
        return old_published or new_published

    def save(self, *args, **kwargs):
        if self._published_old_or_current():
            raise ValidationError(self.published_change_message)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._published_old_or_current():
            raise ValidationError(self.published_delete_message)
        return super().delete(*args, **kwargs)


class Slide(VersionContent):
    published_change_message = "A slide in a published training version cannot be changed."
    published_delete_message = "A slide in a published training version cannot be deleted."
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(TrainingVersion, on_delete=models.PROTECT, related_name="slides")
    position = models.PositiveIntegerField()
    header = models.TextField(blank=True)
    body = models.TextField(blank=True)
    footer = models.TextField(blank=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["version", "position"], name="unique_slide_position"),
            models.CheckConstraint(condition=Q(position__gte=1), name="slide_position_positive"),
        ]


class SupportingMaterial(VersionContent):
    published_change_message = "Supporting material in a published version cannot be changed."
    published_delete_message = "Supporting material in a published version cannot be deleted."

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(TrainingVersion, on_delete=models.PROTECT, related_name="materials")
    position = models.PositiveIntegerField()
    name = models.CharField(max_length=200, blank=True)
    url = models.URLField(max_length=2000, blank=True)
    description = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["version", "position"], name="unique_material_position"),
            models.CheckConstraint(condition=Q(position__gte=1), name="material_position_positive"),
        ]


class Quiz(VersionContent):
    published_change_message = "A quiz in a published version cannot be changed."
    published_delete_message = "A quiz in a published version cannot be deleted."
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.OneToOneField(TrainingVersion, on_delete=models.PROTECT, related_name="quiz", null=True, blank=True)
    title = models.CharField(max_length=200, blank=True)
    # Scopes library quizzes (no version) to demo or real administrators.
    is_demo = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)
    passing_percent = models.PositiveSmallIntegerField(null=True, blank=True)


class Question(VersionContent):
    published_version_lookup = "quiz__version"
    published_change_message = "A question in a published version cannot be changed."
    published_delete_message = "A question in a published version cannot be deleted."
    class Type(models.TextChoices):
        SINGLE_CHOICE = "single_choice", "Single choice"
        TRUE_FALSE = "true_false", "True or false"
        MULTIPLE_SELECTION = "multiple_selection", "Multiple selection"
        ORDERING = "ordering", "Ordering"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quiz = models.ForeignKey(Quiz, on_delete=models.PROTECT, related_name="questions")
    position = models.PositiveIntegerField()
    type = models.CharField(max_length=20, choices=Type.choices)
    prompt = models.TextField(blank=True)

    class Meta:
        ordering = ["position"]
        constraints = [models.UniqueConstraint(fields=["quiz", "position"], name="unique_quiz_question_position")]


class QuestionOption(VersionContent):
    published_version_lookup = "question__quiz__version"
    published_change_message = "An option in a published version cannot be changed."
    published_delete_message = "An option in a published version cannot be deleted."
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="options")
    position = models.PositiveIntegerField()
    text = models.CharField(max_length=500, blank=True)
    is_correct = models.BooleanField(default=False)
    required_rank = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["question", "position"], name="unique_question_option_position"),
            models.UniqueConstraint(
                fields=["question", "required_rank"], condition=Q(required_rank__isnull=False),
                name="unique_required_rank_per_question",
            ),
        ]

class EmployeeProfileChange(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="profile_changes")
    actor = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="profile_edits")
    old_name = models.CharField(max_length=200)
    new_name = models.CharField(max_length=200)
    old_email = models.EmailField()
    new_email = models.EmailField()
    changed_at = models.DateTimeField(auto_now_add=True)


class EmployeeActivityChange(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="activity_changes")
    actor = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="activity_edits")
    was_active = models.BooleanField()
    is_active = models.BooleanField()
    changed_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Employee activity history cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Employee activity history cannot be deleted.")


class Ending(models.TextChoices):
    COMPLETED = "completed", "Completed"
    CANCELED = "canceled", "Canceled"
    WAIVED = "waived", "Waived"


OUTSTANDING = Q(completed_at__isnull=True, canceled_at__isnull=True, waived_at__isnull=True)


def _ending_expression():
    return Case(
        When(canceled_at__isnull=False, then=Value(Ending.CANCELED)),
        When(waived_at__isnull=False, then=Value(Ending.WAIVED)),
        When(completed_at__isnull=False, then=Value(Ending.COMPLETED)),
        default=Value(None), output_field=CharField(),
    )


def assignment_ending(assignment):
    if getattr(assignment, "canceled_at", None):
        return Ending.CANCELED
    if getattr(assignment, "waived_at", None):
        return Ending.WAIVED
    if getattr(assignment, "completed_at", None):
        return Ending.COMPLETED
    return None


class AssignmentQuerySet(models.QuerySet):
    def outstanding(self):
        return self.filter(OUTSTANDING)

    def with_ending(self):
        return self.annotate(ending_value=_ending_expression())

    def with_status(self, *, now):
        """Annotate status codes, mirroring assignment_status() in assignments.py."""
        return self.with_ending().annotate(
            status_state=Coalesce("ending_value", Value("pending"), output_field=CharField()),
            status_progress=Case(
                When(completed_at__isnull=False, then=Value("completed")),
                When(started_at__isnull=False, then=Value("in_progress")),
                default=Value("not_started"), output_field=CharField(),
            ),
            status_deadline=Case(
                When(ending_value__in=[Ending.CANCELED, Ending.WAIVED], then=Value(None)),
                When(completed_at__gte=F("due_at"), then=Value("completed_late")),
                When(completed_at__isnull=False, then=Value("on_time")),
                When(due_at__lte=now, then=Value("overdue")),
                default=Value("on_time"), output_field=CharField(),
            ),
        )

    def ended_by(self, ending):
        return self.with_ending().filter(ending_value=ending)

    def in_state(self, state):
        # These filters mirror assignment_state() in assignments.py.
        if state == "pending":
            return self.outstanding()
        if state == "completed":
            return self.ended_by(Ending.COMPLETED)
        if state == "canceled":
            return self.ended_by(Ending.CANCELED)
        if state == "waived":
            return self.ended_by(Ending.WAIVED)
        raise ValueError(f"Unknown assignment state: {state}")

    def visible_to_learner(self, employee):
        return self.filter(employee=employee, canceled_at__isnull=True, archived_at__isnull=True)


class Assignment(models.Model):
    objects = AssignmentQuerySet.as_manager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="assignments")
    training = models.ForeignKey(Training, on_delete=models.PROTECT, related_name="assignments")
    version = models.ForeignKey(TrainingVersion, on_delete=models.PROTECT, related_name="assignments")
    renews = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="renewals")
    cycle_number = models.PositiveIntegerField(default=1)
    due_date = models.DateField()
    due_timezone = models.CharField(max_length=64)
    due_at = models.DateTimeField()
    due_revision = models.PositiveIntegerField(default=0)
    assigned_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    resume_slide = models.ForeignKey(Slide, on_delete=models.PROTECT, null=True, blank=True, related_name="resume_assignments")
    resume_document = models.ForeignKey("OutlineItem", on_delete=models.PROTECT, null=True, blank=True, related_name="resume_assignments")
    completed_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    waived_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    @property
    def ending(self):
        return assignment_ending(self)

    @property
    def is_outstanding(self):
        return self.ending is None

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "training"],
                condition=OUTSTANDING,
                name="one_pending_assignment_per_employee_training",
            ),
            models.UniqueConstraint(
                fields=["renews"], condition=Q(renews__isnull=False, canceled_at__isnull=True),
                name="one_live_renewal_per_completion",
            ),
            models.CheckConstraint(
                condition=~(Q(completed_at__isnull=False, waived_at__isnull=False)
                            | Q(canceled_at__isnull=False, waived_at__isnull=False)),
                name="waiver_exclusive_ending",
            ),
        ]


class AssignmentHistory(models.Model):
    class Kind(models.TextChoices):
        DEADLINE_CHANGED = "deadline_changed", "Deadline changed"
        CANCELED = "canceled", "Canceled"
        ARCHIVED = "archived", "Archived"
        RESTORED = "restored", "Restored"
        WAIVED = "waived", "Waived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="history")
    actor = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="assignment_changes")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    occurred_at = models.DateTimeField()
    old_due_date = models.DateField(null=True, blank=True)
    new_due_date = models.DateField(null=True, blank=True)
    old_due_timezone = models.CharField(max_length=64, blank=True)
    new_due_timezone = models.CharField(max_length=64, blank=True)
    old_due_at = models.DateTimeField(null=True, blank=True)
    new_due_at = models.DateTimeField(null=True, blank=True)
    old_due_revision = models.PositiveIntegerField(null=True, blank=True)
    new_due_revision = models.PositiveIntegerField(null=True, blank=True)
    old_canceled_at = models.DateTimeField(null=True, blank=True)
    new_canceled_at = models.DateTimeField(null=True, blank=True)
    old_waived_at = models.DateTimeField(null=True, blank=True)
    new_waived_at = models.DateTimeField(null=True, blank=True)
    old_archived_at = models.DateTimeField(null=True, blank=True)
    new_archived_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "new_due_revision"],
                condition=Q(kind="deadline_changed"),
                name="one_deadline_event_per_revision",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Assignment history cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Assignment history cannot be deleted.")


class AssignmentSlideProgress(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="slide_progress")
    slide = models.ForeignKey(Slide, on_delete=models.PROTECT, related_name="assignment_progress")
    acknowledged_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["assignment", "slide"], name="one_acknowledgment_per_assignment_slide"),
        ]


class QuizAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="quiz_attempts")
    quiz = models.ForeignKey(Quiz, on_delete=models.PROTECT, related_name="attempts")
    submission_id = models.UUIDField()
    request_fingerprint = models.CharField(max_length=64)
    correct_count = models.PositiveIntegerField()
    question_count = models.PositiveIntegerField()
    passed = models.BooleanField()
    submitted_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["assignment", "submission_id"], name="one_attempt_per_submission")]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("A submitted quiz attempt cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A submitted quiz attempt cannot be deleted.")


class AttemptResponse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(QuizAttempt, on_delete=models.PROTECT, related_name="responses")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name="attempt_responses")
    selected_option = models.ForeignKey(
        QuestionOption, on_delete=models.PROTECT, related_name="attempt_responses", null=True, blank=True,
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attempt", "question"], name="one_response_per_attempt_question")]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("A submitted answer cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A submitted answer cannot be deleted.")


class AttemptResponseSelection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    response = models.ForeignKey(AttemptResponse, on_delete=models.PROTECT, related_name="selections")
    option = models.ForeignKey(QuestionOption, on_delete=models.PROTECT, related_name="response_selections")
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["response", "option"], name="one_option_per_response"),
            models.UniqueConstraint(fields=["response", "position"], name="one_selection_per_position"),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("A submitted answer cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A submitted answer cannot be deleted.")


class AssignmentOperation(models.Model):
    class Source(models.TextChoices):
        EMPLOYEES = "employees", "Employees"
        GROUP = "group", "Group"
        RENEWAL = "renewal", "Renewal"

    id = models.UUIDField(primary_key=True)
    actor = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="assignment_operations")
    version = models.ForeignKey(TrainingVersion, on_delete=models.PROTECT, related_name="assignment_operations")
    request_fingerprint = models.CharField(max_length=64)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.EMPLOYEES)
    group = models.ForeignKey("EmployeeGroup", on_delete=models.PROTECT, null=True, blank=True,
                              related_name="assignment_operations")
    renewal_as_of = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=(Q(source="group", group__isnull=False, renewal_as_of__isnull=True)
                       | Q(source="employees", group__isnull=True, renewal_as_of__isnull=True)
                       | Q(source="renewal", group__isnull=True, renewal_as_of__isnull=False)),
            name="assignment_operation_source_group_consistent",
        )]


class EmployeeGroup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(Lower("name"), "is_demo", name="employee_group_name_ci_unique")]


class EmployeeGroupMembership(models.Model):
    group = models.ForeignKey(EmployeeGroup, on_delete=models.PROTECT, related_name="memberships")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="group_memberships")
    added_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "employee"], name="one_membership_per_employee_group")]


class EmployeeGroupChange(models.Model):
    class Kind(models.TextChoices):
        CREATED = "created", "Created"
        RENAMED = "renamed", "Renamed"
        MEMBER_ADDED = "member_added", "Member added"
        MEMBER_REMOVED = "member_removed", "Member removed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(EmployeeGroup, on_delete=models.PROTECT, related_name="changes")
    actor = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="group_changes")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    old_name = models.CharField(max_length=200, blank=True)
    new_name = models.CharField(max_length=200, blank=True)
    occurred_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Group history cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Group history cannot be deleted.")


class AssignmentOperationResult(models.Model):
    class Outcome(models.TextChoices):
        CREATED = "created", "Created"
        ALREADY_ASSIGNED = "already_assigned", "Already assigned"
        CONFLICT = "conflict", "Conflict"
        INACTIVE = "inactive", "Inactive"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(AssignmentOperation, on_delete=models.PROTECT, related_name="results")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="assignment_results")
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, null=True, blank=True, related_name="operation_results")
    detail = models.CharField(max_length=240, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["operation", "employee"], name="one_result_per_operation_employee"),
        ]


class Notification(models.Model):
    class Kind(models.TextChoices):
        ASSIGNED = "assigned", "Assignment created"
        DEADLINE_CHANGED = "deadline_changed", "Deadline changed"
        THREE_DAY_REMINDER = "three_day_reminder", "Three-day reminder"
        DUE_DAY_REMINDER = "due_day_reminder", "Due-day reminder"
        MANUAL_NUDGE = "manual_nudge", "Manual nudge"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENDING = "sending", "Sending"
        RETRY = "retry", "Retry scheduled"
        SENT = "sent", "Sent to local sink"
        SUPPRESSED = "suppressed", "Suppressed"
        BLOCKED = "blocked", "Blocked"
        EXHAUSTED = "exhausted", "Retries exhausted"
        AMBIGUOUS = "ambiguous", "Ambiguous outcome"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="notifications")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    due_revision = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(Employee, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="manual_nudge_requests")
    requested_at = models.DateTimeField(null=True, blank=True)
    status_changed_at = models.DateTimeField(default=timezone.now)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_token = models.UUIDField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    automatic_failures = models.PositiveIntegerField(default=0)
    attempt_count = models.PositiveIntegerField(default=0)
    provider_reference = models.CharField(max_length=200, blank=True)
    last_error_code = models.CharField(max_length=80, blank=True)
    suppression_reason = models.CharField(max_length=160, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["assignment", "kind", "due_revision"],
                                    condition=~Q(kind="manual_nudge"),
                                    name="one_notification_per_assignment_kind_revision"),
            models.CheckConstraint(
                condition=(Q(kind="manual_nudge", requested_by__isnull=False, requested_at__isnull=False)
                           | ~Q(kind="manual_nudge")),
                name="manual_nudge_has_requester_and_time",
            ),
        ]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="notification_due_idx")]


class NotificationDeliveryAttempt(models.Model):
    class Outcome(models.TextChoices):
        ACCEPTED = "accepted", "Accepted by local sink"
        TRANSIENT_FAILURE = "transient_failure", "Transient failure"
        PERMANENT_FAILURE = "permanent_failure", "Permanent failure"
        AMBIGUOUS = "ambiguous", "Ambiguous outcome"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notification = models.ForeignKey(Notification, on_delete=models.PROTECT, related_name="delivery_attempts")
    number = models.PositiveIntegerField()
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, blank=True)
    recipient_email = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    due_date = models.DateField()
    due_timezone = models.CharField(max_length=64)
    due_at = models.DateTimeField()
    due_revision = models.PositiveIntegerField()
    delivery_key = models.UUIDField()
    provider_reference = models.CharField(max_length=200, blank=True)
    error_code = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["notification", "number"],
                                               name="one_delivery_attempt_number")]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exclude(outcome="").exists():
            raise ValidationError("A finished delivery attempt cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Delivery attempts cannot be deleted.")


class NotificationReconciliation(models.Model):
    class Decision(models.TextChoices):
        DELIVERED = "delivered", "Confirmed delivered"
        NOT_DELIVERED = "not_delivered", "Confirmed not delivered"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notification = models.ForeignKey(Notification, on_delete=models.PROTECT, related_name="reconciliations")
    actor = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="notification_reconciliations")
    decision = models.CharField(max_length=20, choices=Decision.choices)
    reason = models.TextField()
    decided_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("A reconciliation decision cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("A reconciliation decision cannot be deleted.")


class ImportBatch(models.Model):
    """Short-lived normalized import plan; never an attachment store."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="training_imports")
    plan = models.JSONField(default=list)
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    committed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    result_ids = models.JSONField(default=list)


class OutlineItem(VersionContent):
    """A section or a required document in one training version."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.ForeignKey(TrainingVersion, on_delete=models.PROTECT, related_name="outline_items")
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="children")
    kind = models.CharField(max_length=10, choices=[("section", "Section"), ("document", "Document")])
    position = models.PositiveIntegerField()
    title = models.CharField(max_length=200, blank=True)
    body = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=["section", "document"]), name="valid_outline_kind"),
            models.CheckConstraint(condition=Q(position__gte=1), name="outline_position_positive"),
            models.UniqueConstraint(fields=["version", "parent", "position"], condition=Q(parent__isnull=False), name="unique_outline_sibling"),
            models.UniqueConstraint(fields=["version", "position"], condition=Q(parent__isnull=True), name="unique_outline_root"),
        ]

    @property
    def header(self):
        return self.title

    @property
    def rendered_body(self):
        from .documents import render_body
        return render_body(self.body)


class AssignmentDocumentProgress(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="document_progress")
    document = models.ForeignKey(OutlineItem, on_delete=models.PROTECT, related_name="acknowledgments")
    acknowledged_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["assignment", "document"], name="unique_document_acknowledgment")]

    def save(self, *args, **kwargs):
        if (self.document.kind != "document" or self.document.version_id != self.assignment.version_id
                or self.document.version.status != "published"):
            raise ValidationError("Acknowledge a document in the assigned published version.")
        if self.pk:
            raise ValidationError("Document acknowledgment evidence cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Document acknowledgment evidence cannot be deleted.")


class WorkerHeartbeat(models.Model):
    """When each background worker last finished a cycle, so operators can tell a stalled worker."""
    name = models.CharField(max_length=50, primary_key=True)
    beat_at = models.DateTimeField()


class RateLimitCounter(models.Model):
    """Account requests counted in one fixed window, shared by every process that serves requests."""
    scope = models.CharField(max_length=50)
    # An HMAC-SHA256 of the limited key, keyed from SECRET_KEY: without the secret, nobody can test a guessed email or address.
    key_digest = models.CharField(max_length=64)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scope", "key_digest", "window_start"],
                                               name="unique_rate_limit_window")]
