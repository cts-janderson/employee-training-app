import uuid
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from .models import Question, QuestionOption, Quiz, Slide, SupportingMaterial, Training, TrainingVersion


class DraftError(Exception):
    pass


class DraftConflict(DraftError):
    pass


class Problem(str):
    """A user-facing message that also names the form field it belongs to."""

    def __new__(cls, message, target=None):
        problem = super().__new__(cls, message)
        problem.target = target
        return problem


class DraftInputError(DraftError):
    def __init__(self, message, *, target=None):
        super().__init__(message)
        self.target = target


class PublicationBlocked(DraftInputError):
    def __init__(self, problems):
        self.problems = problems
        super().__init__("The saved draft needs corrections before publication.")


def _locked_draft(version_id, expected_revision, *, stale_message):
    version = TrainingVersion.objects.select_for_update().get(pk=version_id)
    if version.status != TrainingVersion.Status.DRAFT:
        raise DraftConflict("This version has already been published.")
    if version.revision != expected_revision:
        raise DraftConflict(stale_message)
    return version


def create_training(*, is_demo, content_format="slides"):
    if content_format not in {"slides", "documents"}:
        raise DraftInputError("Choose a supported content format.")
    with transaction.atomic():
        training = Training.objects.create(is_demo=is_demo)
        TrainingVersion.objects.create(training=training, version_number=1, content_format=content_format)
        return training


def create_revision(training_id, *, content_format=None):
    with transaction.atomic():
        training = Training.objects.select_for_update().get(pk=training_id)
        if training.versions.filter(status=TrainingVersion.Status.DRAFT).exists():
            raise DraftConflict("Finish the existing draft before starting another revision.")
        source = training.versions.filter(status=TrainingVersion.Status.PUBLISHED).order_by("-version_number").first()
        if source is None:
            raise DraftInputError("Publish the first version before creating a revision.")
        chosen_format = content_format or source.content_format
        if chosen_format not in {"slides", "documents"} or (source.content_format == "documents" and chosen_format != "documents"):
            raise DraftInputError("Document training revisions must keep document content.")
        draft = TrainingVersion.objects.create(
            training=training,
            version_number=source.version_number + 1,
            title=source.title,
            description=source.description,
            content_format=chosen_format,
        )
        if chosen_format == "documents":
            from .documents import copy_into
            copy_into(source, draft)
        else:
            Slide.objects.bulk_create(
                [
                    Slide(version=draft, position=slide.position, header=slide.header, body=slide.body, footer=slide.footer)
                    for slide in source.slides.all()
                ]
            )
        SupportingMaterial.objects.bulk_create([
            SupportingMaterial(version=draft, position=item.position, name=item.name,
                               url=item.url, description=item.description)
            for item in source.materials.all()
        ])
        source_quiz = Quiz.objects.filter(version=source).first()
        if source_quiz is not None:
            copy_quiz(source_quiz, draft)
        return draft


def copy_quiz(source, version):
    copied = Quiz.objects.create(version=version, is_demo=version.training.is_demo,
                                 title=source.title, passing_percent=source.passing_percent)
    for question in source.questions.prefetch_related("options"):
        copied_question = Question.objects.create(
            quiz=copied, position=question.position, type=question.type, prompt=question.prompt,
        )
        QuestionOption.objects.bulk_create([
            QuestionOption(question=copied_question, position=option.position,
                           text=option.text, is_correct=option.is_correct,
                           required_rank=option.required_rank)
            for option in question.options.all()
        ])
    return copied


def attach_library_quiz(*, version_id, quiz_id, expected_version_revision, expected_quiz_revision, confirm):
    from .quiz_authoring import _locked_library_quiz, library_quiz_problems

    with transaction.atomic():
        version = _locked_draft(
            version_id, expected_version_revision,
            stale_message="The training draft changed after attachment review.",
        )
        source = _locked_library_quiz(
            quiz_id, expected_quiz_revision, stale_message="The library quiz changed after attachment review.",
        )
        if source.is_demo != version.training.is_demo:
            raise DraftInputError("Choose a library quiz from your own library.")
        if library_quiz_problems(source):
            raise DraftInputError("The library quiz needs corrections before it can be attached.")
        current = Quiz.objects.filter(version=version).first()
        if confirm != ("replace" if current else "attach"):
            raise DraftConflict("The draft quiz changed after attachment review.")
        if current:
            QuestionOption.objects.filter(question__quiz=current).delete()
            current.questions.all().delete()
            current.delete()
        copied = copy_quiz(source, version)
        version.revision += 1
        version.save(update_fields=["revision"])
        return copied


def read_slide_rows(post):
    fields = [post.getlist(name) for name in ("slide_id", "slide_header", "slide_body", "slide_footer")]
    if len({len(values) for values in fields}) != 1:
        raise DraftInputError("Slide fields are incomplete. Reload the draft and try again.")
    if len(fields[0]) > 100:
        raise DraftInputError("A draft may contain at most 100 slides.")
    rows = []
    for slide_id, header, body, footer in zip(*fields, strict=True):
        try:
            parsed_id = uuid.UUID(slide_id)
        except (ValueError, AttributeError):
            raise DraftInputError("A slide identifier is invalid. Reload the draft and try again.") from None
        rows.append({"id": parsed_id, "header": header, "body": body, "footer": footer})
    if len({row["id"] for row in rows}) != len(rows):
        raise DraftInputError("A slide appears more than once. Reload the draft and try again.")
    return rows


def _apply_action(rows, action):
    if action == "save":
        return rows
    if action == "add":
        if len(rows) >= 100:
            raise DraftInputError("A draft may contain at most 100 slides.")
        return [*rows, {"id": uuid.uuid4(), "header": "", "body": "", "footer": ""}]
    operation, separator, raw_id = action.partition(":")
    if not separator or operation not in {"remove", "up", "down"}:
        raise DraftInputError("Unknown draft action.")
    try:
        target = uuid.UUID(raw_id)
        index = next(index for index, row in enumerate(rows) if row["id"] == target)
    except (ValueError, StopIteration):
        raise DraftInputError("The selected slide was not found. Reload the draft and try again.") from None
    if operation == "remove":
        return rows[:index] + rows[index + 1 :]
    other = index - 1 if operation == "up" else index + 1
    if 0 <= other < len(rows):
        rows[index], rows[other] = rows[other], rows[index]
    return rows


def _publication_problems(title, rows):
    problems = []
    if not title.strip():
        problems.append(Problem("Add a training title.", ("title",)))
    if not rows:
        problems.append("Add at least one slide.")
    for index, row in enumerate(rows, start=1):
        if not row["header"].strip():
            problems.append(Problem(f"Slide {index}: add a header.", ("slide", index, "header")))
        if not row["body"].strip():
            problems.append(Problem(f"Slide {index}: add a body.", ("slide", index, "body")))
    return problems


def publication_problems(version):
    from .quiz_authoring import quiz_publication_problems

    if version.content_format == "documents":
        from .documents import publication_problems as document_problems
        content_problems = document_problems(version)
    else:
        content_problems = _publication_problems(version.title, list(version.slides.values("header", "body")))
    return (content_problems + quiz_publication_problems(version)
            + _material_problems(list(version.materials.values("name", "url"))))


def _material_problems(rows):
    problems = []
    for index, row in enumerate(rows, 1):
        if not row["name"].strip():
            problems.append(Problem(f"Material {index}: add a name.", ("material", index, "name")))
        if not row["url"].strip():
            problems.append(Problem(f"Material {index}: add a link.", ("material", index, "url")))
        else:
            try:
                parsed = urlsplit(row["url"].strip())
                valid = parsed.scheme.lower() == "https" and bool(parsed.hostname)
            except ValueError:
                valid = False
            if not valid:
                problems.append(Problem(f"Material {index}: use an https:// link.", ("material", index, "url")))
    return problems


def read_material_rows(post):
    fields = [post.getlist(name) for name in ("material_id", "material_name", "material_url",
                                              "material_description")]
    if len({len(items) for items in fields}) != 1:
        raise DraftInputError("Material fields are incomplete. Reload the draft and try again.")
    if len(fields[0]) > 100:
        raise DraftInputError("A draft may contain at most 100 materials.")
    rows = []
    for index, (item_id, name, url, description) in enumerate(zip(*fields, strict=True), 1):
        try:
            item_id = uuid.UUID(item_id)
        except (ValueError, TypeError, AttributeError):
            raise DraftInputError("A material identifier is invalid. Reload the draft and try again.") from None
        for field, value, maximum in (("name", name, 200), ("url", url, 2000),
                                      ("description", description, 500)):
            if len(value) > maximum:
                raise DraftInputError("A material field is too long.", target=("material", index, field))
        rows.append({"id": item_id, "name": name, "url": url, "description": description})
    if len({row["id"] for row in rows}) != len(rows):
        raise DraftInputError("A material appears more than once. Reload the draft and try again.")
    return rows


def save_materials(*, version_id, expected_revision, rows, action):
    if len(rows) > 100:
        raise DraftInputError("A draft may contain at most 100 materials.")
    for index, row in enumerate(rows, 1):
        for field, maximum in (("name", 200), ("url", 2000), ("description", 500)):
            if len(row[field]) > maximum:
                raise DraftInputError("A material field is too long.", target=("material", index, field))
    with transaction.atomic():
        version = _locked_draft(version_id, expected_revision,
                                stale_message="A newer draft was saved. Reload before editing again.")
        current_ids = set(version.materials.values_list("id", flat=True))
        if len(rows) != len(current_ids) or {row["id"] for row in rows} != current_ids:
            raise DraftInputError("The material list changed. Reload the draft and try again.")
        if action == "add":
            if len(rows) >= 100:
                raise DraftInputError("A draft may contain at most 100 materials.")
            rows = [*rows, {"id": uuid.uuid4(), "name": "", "url": "", "description": ""}]
        elif action != "save":
            operation, _, raw_id = action.partition(":")
            if operation not in {"remove", "up", "down"}:
                raise DraftInputError("Unknown material action.")
            try:
                target = uuid.UUID(raw_id)
                index = next(index for index, row in enumerate(rows) if row["id"] == target)
            except (ValueError, StopIteration):
                raise DraftInputError("The selected material was not found.") from None
            if operation == "remove":
                rows = rows[:index] + rows[index + 1:]
            else:
                other = index - 1 if operation == "up" else index + 1
                if 0 <= other < len(rows):
                    rows[index], rows[other] = rows[other], rows[index]
        version.revision += 1
        version.save(update_fields=["revision"])
        version.materials.all().delete()
        SupportingMaterial.objects.bulk_create([
            SupportingMaterial(id=row["id"], version=version, position=index,
                               name=row["name"], url=row["url"], description=row["description"])
            for index, row in enumerate(rows, 1)
        ])
        return version


def publish_saved_draft(*, version_id, expected_revision):
    with transaction.atomic():
        version = TrainingVersion.objects.select_for_update().get(pk=version_id)
        if version.revision != expected_revision:
            raise DraftConflict("A newer draft was saved after this review.")
        if version.status == TrainingVersion.Status.PUBLISHED:
            return version
        problems = publication_problems(version)
        if problems:
            raise PublicationBlocked(problems)
        version.status = TrainingVersion.Status.PUBLISHED
        version.published_at = timezone.now()
        version.save(update_fields=["status", "published_at"])
        return version


def save_draft(*, version_id, expected_revision, title, description, rows, action):
    if len(title) > 200 or len(description) > 5000:
        raise DraftInputError("The title or description is too long.",
                              target=("title",) if len(title) > 200 else ("description",))
    for position, row in enumerate(rows, 1):
        for field, limit in (("header", 200), ("body", 10000), ("footer", 500)):
            if len(row[field]) > limit:
                raise DraftInputError("A slide field is too long.", target=("slide", position, field))
    with transaction.atomic():
        version = _locked_draft(
            version_id, expected_revision,
            stale_message="A newer draft was saved. Reload before editing again.",
        )
        if version.content_format != "slides":
            raise DraftInputError("Use the document editor for this version.")
        existing_ids = set(version.slides.values_list("id", flat=True))
        if {row["id"] for row in rows} != existing_ids or len(rows) != len(existing_ids):
            raise DraftInputError("The slide list changed. Reload the draft and try again.")
        rows = _apply_action(rows, action)
        version.title = title
        version.description = description
        version.revision += 1
        version.save(update_fields=["title", "description", "revision"])
        version.slides.all().delete()
        Slide.objects.bulk_create(
            [
                Slide(
                    id=row["id"], version=version, position=index,
                    header=row["header"], body=row["body"], footer=row["footer"],
                )
                for index, row in enumerate(rows, start=1)
            ]
        )
        return version, []
