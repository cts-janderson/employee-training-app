from zoneinfo import ZoneInfo

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import Question
from .question_types import for_display


def save_message(revision):
    saved_at = timezone.localtime(timezone.now(), ZoneInfo(settings.ORG_TIME_ZONE))
    return f"Saved revision {revision} at {saved_at:%Y-%m-%d %H:%M:%S %Z}."


def record_save(request, key, revision):
    request.session[key] = save_message(revision)


def quiz_focus_target(quiz, action, *, before=None):
    """Return the saved control to focus after a structural editor action."""
    if action == "add_quiz":
        return "passing-percent"
    if quiz is None:
        return "editor-form"
    questions = list(quiz.questions.prefetch_related("options"))
    if action in {"add_single", "add_true_false", "add_multiple", "add_ordering"}:
        return f"prompt-{questions[-1].id}" if questions else "quiz-actions"
    operation, separator, raw_id = action.partition(":")
    if not separator:
        return ""
    target = raw_id.split(":", 1)[0]
    if operation == "add_option":
        question = next((row for row in questions if str(row.id) == target), None)
        if question:
            options = list(question.options.all())
            return f"option-{options[-1].id}" if options else f"prompt-{question.id}"
    if operation in {"up_question", "down_question", "change_type"}:
        return f"question-heading-{target}"
    if operation == "remove_question":
        old_questions = list(before.questions.all()) if before else []
        old_index = next((index for index, row in enumerate(old_questions) if str(row.id) == target), 0)
        return f"question-heading-{questions[min(old_index, len(questions) - 1)].id}" if questions else "quiz-actions"
    if operation == "remove_option":
        for question in before.questions.all() if before else []:
            if any(str(option.id) == target for option in question.options.all()):
                return f"prompt-{question.id}"
        return "quiz-actions"
    return ""


def submitted_slide_rows(post):
    ids = post.getlist("slide_id")
    headers = post.getlist("slide_header")
    bodies = post.getlist("slide_body")
    footers = post.getlist("slide_footer")
    return [
        {
            "id": slide_id, "header": headers[index] if index < len(headers) else "",
            "body": bodies[index] if index < len(bodies) else "",
            "footer": footers[index] if index < len(footers) else "",
            "position": index + 1,
        }
        for index, slide_id in enumerate(ids[:100])
    ]


def saved_slide_rows(version):
    return [
        {
            "id": str(slide.id), "header": slide.header, "body": slide.body,
            "footer": slide.footer, "position": slide.position,
        }
        for slide in version.slides.all()
    ]


def draft_errors(messages, slides, training_id, *, title="", description=""):
    links = []
    title_error = ""
    description_error = ""
    for message in messages:
        target = getattr(message, "target", None)
        href = "#draft-actions"
        if target == ("title",):
            href = "#title"
            title_error = message
        elif target == ("description",):
            href = "#description"
            description_error = message
        elif target and target[0] == "slide":
            _, position, field = target
            slide = next((row for row in slides if row["position"] == position), None)
            if slide:
                href = f"#{field}-{slide['id']}" if field else f"#slide-{slide['id']}"
                if field:
                    slide[f"{field}_error"] = message
        elif target and target[0] == "quiz":
            href = reverse("quiz_editor", args=[training_id])
            href += "#passing-percent" if target[1] == "threshold" else "#quiz-actions"
        elif target and target[0] == "question":
            href = reverse("quiz_editor", args=[training_id]) + f"#question-{target[1]}"
        elif target and target[0] == "material":
            href = reverse("draft_editor", args=[training_id]) + "#supporting-material"
        links.append({"message": message, "href": href})
    return links, title_error, description_error


def saved_question_rows(quiz):
    if quiz is None:
        return []
    labels = dict(Question.Type.choices)
    return [
        {
            "id": str(question.id), "position": question.position,
            "type": question.type, "type_label": labels.get(question.type, question.type),
            "editor_legend": for_display(question.type).editor_legend,
            "editor_template": for_display(question.type).editor_template,
            "fixed_options": for_display(question.type).fixed_options,
            "ranked": for_display(question.type).ranked,
            "alternative_type": (Question.Type.MULTIPLE_SELECTION if question.type == Question.Type.SINGLE_CHOICE
                                 else Question.Type.SINGLE_CHOICE if question.type == Question.Type.MULTIPLE_SELECTION
                                 else ""),
            "alternative_label": ("multiple selection" if question.type == Question.Type.SINGLE_CHOICE
                                  else "single choice"),
            "prompt": question.prompt,
            "ready": bool(question.prompt.strip()) and not for_display(question.type).content_problems(
                question.position, list(question.options.all())),
            "options": [
                {
                    "id": str(option.id), "position": option.position,
                    "text": option.text, "rank": option.required_rank or "",
                    "correct": option.is_correct,
                }
                for option in question.options.all()
            ],
        }
        for question in quiz.questions.all()
    ]


def submitted_question_rows(post):
    labels = dict(Question.Type.choices)
    ids = post.getlist("question_id")[:100]
    types = post.getlist("question_type")
    prompts = post.getlist("question_prompt")
    rows = []
    for index, question_id in enumerate(ids):
        kind = types[index] if index < len(types) else ""
        presentation = for_display(kind)
        option_ids = post.getlist(f"option_id_{question_id}")[:20]
        option_texts = post.getlist(f"option_text_{question_id}")
        ranks = post.getlist(f"rank_{question_id}")
        selected = set(post.getlist(f"correct_set_{question_id}"))
        single_selected = post.get(f"correct_{question_id}", "")
        if single_selected:
            selected.add(single_selected)
        rows.append({
            "id": question_id, "position": index + 1,
            "type": kind, "type_label": labels.get(kind, kind),
            "editor_legend": presentation.editor_legend,
            "editor_template": presentation.editor_template,
            "fixed_options": presentation.fixed_options,
            "ranked": presentation.ranked,
            "alternative_type": (Question.Type.MULTIPLE_SELECTION if kind == Question.Type.SINGLE_CHOICE
                                 else Question.Type.SINGLE_CHOICE if kind == Question.Type.MULTIPLE_SELECTION
                                 else ""),
            "alternative_label": ("multiple selection" if kind == Question.Type.SINGLE_CHOICE
                                  else "single choice"),
            "prompt": prompts[index] if index < len(prompts) else "",
            "option_field_count_mismatch": len(option_ids) != len(option_texts),
            "rank_field_count_mismatch": kind == Question.Type.ORDERING and len(ranks) != len(option_ids),
            "invalid_correct": bool(selected - set(option_ids)),
            "options": [
                {
                    "id": option_id, "position": option_index + 1,
                    "text": option_texts[option_index] if option_index < len(option_texts) else "",
                    "rank": ranks[option_index] if option_index < len(ranks) else "",
                    "correct": option_id in selected,
                }
                for option_index, option_id in enumerate(option_ids)
            ],
        })
    return rows


def quiz_errors(messages, questions):
    links = []
    threshold_error = ""
    for message in messages:
        target = getattr(message, "target", None)
        href = "#quiz-actions"
        if target == ("quiz", "threshold"):
            href = "#passing-percent"
            threshold_error = message
        elif target and target[0] == "question":
            question = next((row for row in questions if str(row["id"]) == str(target[1])), None)
            if question is None and isinstance(target[1], int) and 1 <= target[1] <= len(questions):
                question = questions[target[1] - 1]
            if question:
                href = f"#prompt-{question['id']}"
                question["error"] = message
        elif target and target[0] in {"option", "rank"}:
            option = next((option for question in questions for option in question["options"]
                           if str(option["id"]) == str(target[1])), None)
            if option:
                href = f"#{target[0]}-{option['id']}"
                option["error"] = message
        links.append({"message": message, "href": href})
    return links, threshold_error
