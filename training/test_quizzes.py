import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignments import acknowledge_slide, create_assignments
from .models import (
    Assignment, AttemptResponse, AttemptResponseSelection, Employee, Question, QuestionOption,
    Quiz, QuizAttempt, Slide, Training, TrainingVersion,
)
from .quizzes import QuizSubmissionError, displayed_score, presentation_options, submit_quiz


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test", ORG_TIME_ZONE="America/New_York")
class QuizWorkflowTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(
            name="Demo Administrator", email="demo.admin@example.test",
            role=Employee.Role.ADMINISTRATOR, is_demo=True,
        )
        self.employee = Employee.objects.create(name="Learner", email="learner@example.test", is_demo=True)
        self.training = Training.objects.create()
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        self.slide = Slide.objects.create(version=self.version, position=1, header="Read this", body="Important material")

    def quiz_payload(self, action="save", *, threshold="50", edits=None):
        self.version.refresh_from_db()
        quiz = Quiz.objects.filter(version=self.version).first()
        payload = {"revision": str(self.version.revision), "action": action, "passing_percent": threshold,
                   "question_id": [], "question_type": [], "question_prompt": []}
        if quiz:
            for question in quiz.questions.prefetch_related("options"):
                edit = (edits or {}).get(question.position, {})
                payload["question_id"].append(str(question.id))
                payload["question_type"].append(question.type)
                payload["question_prompt"].append(edit.get("prompt", question.prompt))
                options = list(question.options.all())
                payload[f"option_id_{question.id}"] = [str(option.id) for option in options]
                payload[f"option_text_{question.id}"] = edit.get("texts", [option.text for option in options])
                if question.type == Question.Type.MULTIPLE_SELECTION:
                    indexes = edit.get("correct_indexes")
                    payload[f"correct_set_{question.id}"] = (
                        [str(options[index].id) for index in indexes] if indexes is not None else
                        [str(option.id) for option in options if option.is_correct]
                    )
                elif question.type == Question.Type.ORDERING:
                    ranks = edit.get("ranks", [option.required_rank for option in options])
                    payload[f"rank_{question.id}"] = ["" if rank is None else str(rank) for rank in ranks]
                else:
                    correct_index = edit.get("correct_index")
                    chosen = options[correct_index] if correct_index is not None else next((option for option in options if option.is_correct), None)
                    if chosen:
                        payload[f"correct_{question.id}"] = str(chosen.id)
        return payload

    def edit_quiz(self, action="save", *, threshold="50", edits=None):
        return self.client.post(reverse("quiz_editor", args=[self.training.id]),
                                self.quiz_payload(action, threshold=threshold, edits=edits))

    def create_mixed_quiz(self, threshold="50"):
        self.assertEqual(self.edit_quiz("add_quiz").status_code, 302)
        self.assertEqual(self.edit_quiz("add_single", threshold=threshold).status_code, 302)
        self.assertEqual(self.edit_quiz("add_true_false", threshold=threshold, edits={
            1: {"prompt": "Choose the safe action", "texts": ["Report it", "Ignore it"], "correct_index": 0},
        }).status_code, 302)
        self.assertEqual(self.edit_quiz(threshold=threshold, edits={
            2: {"prompt": "Report incidents promptly", "correct_index": 0},
        }).status_code, 302)
        self.version.refresh_from_db()
        return self.version.quiz

    def create_four_format_quiz(self, threshold="75"):
        quiz = self.create_mixed_quiz(threshold=threshold)
        self.assertEqual(self.edit_quiz("add_multiple", threshold=threshold).status_code, 302)
        multiple = quiz.questions.get(position=3)
        self.assertEqual(self.edit_quiz(f"add_option:{multiple.id}", threshold=threshold).status_code, 302)
        self.assertEqual(self.edit_quiz("add_ordering", threshold=threshold, edits={
            3: {"prompt": "Select protective gear", "texts": ["Gloves", "Goggles", "Sandals"],
                "correct_indexes": [0, 1]},
        }).status_code, 302)
        self.assertEqual(self.edit_quiz(threshold=threshold, edits={
            4: {"prompt": "Order the response steps", "texts": ["Report", "Review", "Secure"],
                "ranks": [2, 3, 1]},
        }).status_code, 302)
        return quiz

    def publish(self):
        self.version.refresh_from_db()
        review_url = reverse("publication_review", args=[self.training.id])
        response = self.client.get(review_url)
        if b'name="version_id"' in response.content:
            response = self.client.post(review_url, {
                "version_id": str(self.version.id), "revision": self.version.revision,
            })
        self.version.refresh_from_db()
        return response

    def assign_and_acknowledge(self):
        due_date = (timezone.localdate() + timedelta(days=1)).isoformat()
        create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                           employee_ids=[self.employee.id], due_date_value=due_date)
        assignment = Assignment.objects.get(employee=self.employee)
        acknowledge_slide(assignment_id=assignment.id, slide_id=self.slide.id, employee=self.employee)
        assignment.refresh_from_db()
        return assignment

    def test_mixed_quiz_fail_retry_pass_and_review_without_answer_keys(self):
        quiz = self.create_mixed_quiz()
        publication_review = self.client.get(reverse("publication_review", args=[self.training.id]))
        self.assertContains(publication_review, "2 questions")
        self.assertContains(publication_review, "Passing threshold: 50%")
        self.assertContains(publication_review, "Choose the safe action")
        self.assertContains(publication_review, "Report it")
        self.assertEqual(self.publish().status_code, 302)
        assignment = self.assign_and_acknowledge()
        self.assertIsNone(assignment.completed_at)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        quiz_page = self.client.get(reverse("take_quiz", args=[assignment.id]))
        self.assertContains(quiz_page, "Choose the safe action")
        self.assertNotContains(quiz_page, "is_correct")
        self.assertNotContains(quiz_page, "correct_", html=False)
        questions = list(quiz.questions.prefetch_related("options"))
        first_options = list(questions[0].options.all())
        second_options = list(questions[1].options.all())
        wrong_submission = uuid.uuid4()
        wrong = {f"answer_{questions[0].id}": str(first_options[1].id),
                 f"answer_{questions[1].id}": str(second_options[1].id),
                 "submission_id": str(wrong_submission)}
        response = self.client.post(reverse("submit_quiz", args=[assignment.id]), wrong)
        self.assertEqual(response.status_code, 302)
        failed = QuizAttempt.objects.get(submission_id=wrong_submission)
        self.assertFalse(failed.passed)
        self.assertEqual((failed.correct_count, failed.question_count), (0, 2))
        result_page = self.client.get(reverse("quiz_result", args=[assignment.id, failed.id]))
        self.assertContains(result_page, "Your answer: Ignore it")
        self.assertContains(result_page, "Did not pass")
        self.assertContains(result_page, "Try again")
        self.assertContains(result_page, "review attempt history")
        self.assertNotContains(result_page, " — correct")
        assignment.refresh_from_db()
        self.assertIsNone(assignment.completed_at)
        self.assertContains(self.client.get(reverse("assignment_detail", args=[assignment.id])), "Try quiz again")
        good_submission = uuid.uuid4()
        good = {f"answer_{questions[0].id}": str(first_options[0].id),
                f"answer_{questions[1].id}": str(second_options[1].id),
                "submission_id": str(good_submission)}
        self.assertEqual(self.client.post(reverse("submit_quiz", args=[assignment.id]), good).status_code, 302)
        passed = QuizAttempt.objects.get(submission_id=good_submission)
        self.assertTrue(passed.passed)
        self.assertContains(self.client.get(reverse("quiz_result", args=[assignment.id, passed.id])),
                            "Your assignment is complete")
        self.assertEqual((passed.correct_count, passed.question_count), (1, 2))
        self.assertEqual(displayed_score(passed), 50)
        assignment.refresh_from_db()
        self.assertEqual(assignment.completed_at, passed.submitted_at)
        self.assertEqual(QuizAttempt.objects.filter(assignment=assignment).count(), 2)
        detail = self.client.get(reverse("assignment_detail", args=[assignment.id]))
        self.assertContains(detail, "Attempt history")
        self.assertContains(detail, "Did not pass")
        self.assertContains(detail, "Passed")

    def test_final_slide_acknowledgment_opens_quiz_as_next_action(self):
        self.create_mixed_quiz()
        self.publish()
        due_date = (timezone.localdate() + timedelta(days=1)).isoformat()
        create_assignments(
            operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
            employee_ids=[self.employee.id], due_date_value=due_date,
        )
        assignment = Assignment.objects.get(employee=self.employee)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        acknowledgment = self.client.post(reverse("acknowledge", args=[assignment.id, self.slide.id]))
        self.assertRedirects(acknowledgment, reverse("assignment_detail", args=[assignment.id]) + "?slide_acknowledged=1")
        detail = self.client.get(acknowledgment.url)
        self.assertContains(detail, "Slides complete — take the quiz")
        self.assertContains(detail, reverse("take_quiz", args=[assignment.id]))
        assignment.refresh_from_db()
        self.assertIsNone(assignment.completed_at)

    def test_submission_replay_preserves_attempt_and_completion_and_rejects_new_attempt(self):
        quiz = self.create_mixed_quiz()
        self.publish()
        assignment = self.assign_and_acknowledge()
        questions = list(quiz.questions.prefetch_related("options"))
        answers = {question.id: question.options.first().id for question in questions}
        key = uuid.uuid4()
        first = submit_quiz(assignment_id=assignment.id, employee=self.employee,
                            submission_id=key, answers=answers)
        assignment.refresh_from_db()
        completed_at = assignment.completed_at
        replay = submit_quiz(assignment_id=assignment.id, employee=self.employee,
                             submission_id=key, answers=answers, now=timezone.now() + timedelta(days=1))
        self.assertEqual(replay.id, first.id)
        self.assertEqual(QuizAttempt.objects.count(), 1)
        assignment.refresh_from_db()
        self.assertEqual(assignment.completed_at, completed_at)
        with self.assertRaises(QuizSubmissionError):
            submit_quiz(assignment_id=assignment.id, employee=self.employee,
                        submission_id=uuid.uuid4(), answers=answers)
        with self.assertRaises(QuizSubmissionError):
            submit_quiz(assignment_id=assignment.id, employee=self.employee,
                        submission_id=key, answers={questions[0].id: questions[0].options.last().id,
                                                    questions[1].id: questions[1].options.first().id})
        self.assertEqual(AttemptResponse.objects.count(), 2)
        with self.assertRaises(ValidationError):
            first.correct_count = 0
            first.save()

    def test_unfinished_quiz_draft_blocks_publication_then_revision_copies_answer_rules(self):
        self.edit_quiz("add_quiz")
        self.assertContains(self.publish(), "Quiz: add at least one question")
        self.assertContains(self.client.get(reverse("publication_review", args=[self.training.id])),
                            reverse("quiz_editor", args=[self.training.id]) + "#quiz-actions")
        self.edit_quiz("add_single", threshold="")
        self.version.refresh_from_db()
        response = self.publish()
        self.assertContains(response, "Quiz: set an integer passing threshold")
        self.assertContains(response, "Question 1: add a prompt")
        self.assertContains(response, "Question 1: add at least two filled options")
        self.assertEqual(self.version.status, TrainingVersion.Status.DRAFT)
        self.assertContains(self.client.get(reverse("quiz_editor", args=[self.training.id])), "Question 1")
        self.edit_quiz(threshold="101", edits={1: {"prompt": "Question", "texts": ["Yes", "No"], "correct_index": 0}})
        self.assertIsNone(self.version.quiz.passing_percent)
        self.assertEqual(self.edit_quiz(threshold="50", edits={1: {
            "prompt": "Question", "texts": ["Yes", "No"],
        }}).status_code, 302)
        self.assertContains(self.publish(), "Question 1: select exactly one correct answer")
        self.assertEqual(self.edit_quiz(threshold="50", edits={1: {
            "prompt": "Question", "texts": ["Yes", "No"], "correct_index": 0,
        }}).status_code, 302)
        cached_quiz = Quiz.objects.select_related("version").get(version=self.version)
        cached_option = QuestionOption.objects.select_related("question__quiz__version").get(
            question__quiz=cached_quiz, is_correct=True,
        )
        self.assertEqual(self.publish().status_code, 302)
        with self.assertRaises(ValidationError):
            cached_quiz.passing_percent = 90
            cached_quiz.save()
        with self.assertRaises(ValidationError):
            cached_option.is_correct = False
            cached_option.save()
        self.assertEqual(self.client.post(reverse("new_revision", args=[self.training.id])).status_code, 302)
        revision = self.training.versions.get(version_number=2)
        self.assertEqual(revision.quiz.passing_percent, 50)
        self.assertEqual(revision.quiz.questions.count(), 1)
        self.assertNotEqual(revision.quiz.questions.first().id, self.version.quiz.questions.first().id)
        self.assertEqual(list(revision.quiz.questions.first().options.values_list("is_correct", flat=True)), [True, False])
        with self.assertRaises(ValidationError):
            published_question = self.version.quiz.questions.first()
            published_question.prompt = "Changed"
            published_question.save()

    def test_incomplete_or_foreign_answers_do_not_create_attempts(self):
        quiz = self.create_mixed_quiz()
        self.publish()
        due_date = (timezone.localdate() + timedelta(days=1)).isoformat()
        create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                           employee_ids=[self.employee.id], due_date_value=due_date)
        assignment = Assignment.objects.get(employee=self.employee)
        questions = list(quiz.questions.prefetch_related("options"))
        answers = {question.id: question.options.first().id for question in questions}
        with self.assertRaises(QuizSubmissionError):
            submit_quiz(assignment_id=assignment.id, employee=self.employee,
                        submission_id=uuid.uuid4(), answers=answers)
        acknowledge_slide(assignment_id=assignment.id, slide_id=self.slide.id, employee=self.employee)
        with self.assertRaises(QuizSubmissionError):
            submit_quiz(assignment_id=assignment.id, employee=self.employee,
                        submission_id=uuid.uuid4(), answers={questions[0].id: answers[questions[0].id]})
        with self.assertRaises(QuizSubmissionError):
            submit_quiz(assignment_id=assignment.id, employee=self.employee,
                        submission_id=uuid.uuid4(), answers={questions[0].id: answers[questions[1].id],
                                                            questions[1].id: answers[questions[1].id]})
        self.assertEqual(QuizAttempt.objects.count(), 0)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        self.assertEqual(self.client.post(reverse("submit_quiz", args=[assignment.id]), {
            "submission_id": str(uuid.uuid4()), "score": "100",
        }).status_code, 400)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.admin.id)})
        self.assertEqual(self.client.get(reverse("quiz_result", args=[assignment.id, uuid.uuid4()])).status_code, 404)
        self.assertEqual(self.client.post(reverse("submit_quiz", args=[assignment.id]), {
            "submission_id": str(uuid.uuid4()),
        }).status_code, 403)

    def test_unrounded_score_fails_below_threshold_even_when_display_rounds_up(self):
        self.create_mixed_quiz(threshold="67")
        self.assertEqual(self.edit_quiz("add_single", threshold="67").status_code, 302)
        self.assertEqual(self.edit_quiz(threshold="67", edits={3: {
            "prompt": "Third question", "texts": ["Right", "Wrong"], "correct_index": 0,
        }}).status_code, 302)
        self.publish()
        assignment = self.assign_and_acknowledge()
        questions = list(self.version.quiz.questions.prefetch_related("options"))
        answers = {question.id: question.options.first().id for question in questions}
        answers[questions[2].id] = questions[2].options.last().id
        attempt = submit_quiz(assignment_id=assignment.id, employee=self.employee,
                              submission_id=uuid.uuid4(), answers=answers)
        self.assertEqual(attempt.correct_count, 2)
        self.assertEqual(str(displayed_score(attempt)), "66.7")
        self.assertFalse(attempt.passed)
        assignment.refresh_from_db()
        self.assertIsNone(assignment.completed_at)

    def test_quiz_editor_reorders_questions_and_options_without_losing_saved_answers(self):
        quiz = self.create_mixed_quiz()
        questions = list(quiz.questions.all())
        self.assertEqual(self.edit_quiz(f"up_question:{questions[1].id}").status_code, 302)
        self.assertEqual(list(quiz.questions.values_list("id", flat=True)), [questions[1].id, questions[0].id])
        single = quiz.questions.get(type=Question.Type.SINGLE_CHOICE)
        self.assertEqual(self.edit_quiz(f"add_option:{single.id}").status_code, 302)
        single.refresh_from_db()
        self.assertEqual(single.options.count(), 3)
        extra = single.options.last()
        self.assertEqual(self.edit_quiz(f"remove_option:{extra.id}").status_code, 302)
        self.assertEqual(single.options.count(), 2)
        self.assertEqual(list(single.options.values_list("is_correct", flat=True)), [True, False])

    def test_quiz_editor_retains_fields_and_links_duplicate_rank_error(self):
        quiz = self.create_four_format_quiz()
        self.version.refresh_from_db()
        saved = self.client.get(reverse("quiz_editor", args=[self.training.id]))
        self.assertContains(saved, "Saved at")
        ordering = quiz.questions.get(type=Question.Type.ORDERING)
        options = list(ordering.options.all())
        payload = self.quiz_payload(edits={
            1: {"prompt": "Unsaved safe action"},
            4: {"ranks": [1, 1, 3]},
        })
        response = self.client.post(reverse("quiz_editor", args=[self.training.id]), payload)
        self.assertContains(response, "Each ordering rank must be used only once", status_code=400)
        self.assertContains(response, f'href="#rank-{options[1].id}"', status_code=400)
        self.assertContains(response, "Unsaved safe action", status_code=400)
        self.assertContains(response, 'data-unsaved="true"', status_code=400)
        self.assertEqual(response.context["questions"][3]["options"][1]["rank"], "1")
        self.assertEqual(quiz.questions.get(position=1).prompt, "Choose the safe action")

    def test_second_question_long_option_links_exact_field(self):
        quiz = self.create_mixed_quiz()
        second = quiz.questions.get(position=2)
        option = second.options.first()
        payload = self.quiz_payload()
        payload[f"option_text_{second.id}"][0] = "x" * 501
        response = self.client.post(reverse("quiz_editor", args=[self.training.id]), payload)
        self.assertContains(response, f'href="#option-{option.id}"', status_code=400)

    def test_stale_invalid_quiz_submission_keeps_conflict_precedence(self):
        quiz = self.create_mixed_quiz()
        payload = self.quiz_payload()
        payload["revision"] = self.version.revision - 1
        payload["question_prompt"] = []
        response = self.client.post(reverse("quiz_editor", args=[self.training.id]), payload)
        self.assertEqual(response.status_code, 409)
        question = quiz.questions.first()
        payload = self.quiz_payload(action=f"review_remove_question:{question.id}")
        payload["revision"] = self.version.revision - 1
        self.assertEqual(self.client.post(reverse("quiz_editor", args=[self.training.id]), payload).status_code, 409)

    def test_quiz_removal_review_cancel_and_confirm_keep_other_edits(self):
        quiz = self.create_mixed_quiz()
        self.version.refresh_from_db()
        question = quiz.questions.get(position=2)
        payload = self.quiz_payload(edits={1: {"prompt": "Unsaved prompt"}})
        url = reverse("quiz_editor", args=[self.training.id])
        review = self.client.post(url, {**payload, "action": f"review_remove_question:{question.id}"})
        self.assertContains(review, "Confirm removal")
        self.assertContains(review, "Unsaved prompt")
        self.assertEqual(quiz.questions.count(), 2)
        canceled = self.client.post(url, {**payload, "action": "cancel_remove"})
        self.assertContains(canceled, "Unsaved prompt")
        self.assertNotContains(canceled, "Confirm removal")
        self.assertEqual(quiz.questions.count(), 2)
        confirmed = self.client.post(url, {**payload, "action": f"remove_question:{question.id}"})
        self.assertEqual(confirmed.status_code, 302)
        self.assertEqual(quiz.questions.count(), 1)
        self.assertEqual(quiz.questions.get().prompt, "Unsaved prompt")
        self.version.refresh_from_db()
        self.assertContains(self.client.get(url), "Saved at")

    def test_stale_quiz_shows_saved_and_submitted_content_before_apply(self):
        quiz = self.create_mixed_quiz()
        self.version.refresh_from_db()
        stale = self.quiz_payload(edits={1: {"prompt": "My unsaved prompt"}})
        self.assertEqual(self.edit_quiz(edits={1: {"prompt": "Server prompt"}}).status_code, 302)
        url = reverse("quiz_editor", args=[self.training.id])
        conflict = self.client.post(url, stale)
        self.assertContains(conflict, "Server prompt", status_code=409)
        self.assertContains(conflict, "My unsaved prompt", status_code=409)
        self.version.refresh_from_db()
        self.assertContains(conflict, "Replace the saved quiz with my changes", status_code=409)
        self.assertEqual(quiz.questions.get(position=1).prompt, "Server prompt")
        applied = self.client.post(url, {**stale, "revision": str(self.version.revision)})
        self.assertEqual(applied.status_code, 302)
        self.assertEqual(quiz.questions.get(position=1).prompt, "My unsaved prompt")

    def test_option_and_quiz_removal_require_review(self):
        quiz = self.create_mixed_quiz()
        question = quiz.questions.get(position=1)
        option = question.options.first()
        payload = self.quiz_payload()
        url = reverse("quiz_editor", args=[self.training.id])
        option_review = self.client.post(url, {**payload, "action": f"review_remove_option:{option.id}"})
        self.assertContains(option_review, "Confirm removal")
        self.assertEqual(question.options.count(), 2)
        quiz_review = self.client.post(url, {**payload, "action": "review_remove_quiz"})
        self.assertContains(quiz_review, "Remove the entire quiz?")
        self.assertIsNotNone(Quiz.objects.filter(pk=quiz.id).first())
        self.assertEqual(self.client.post(url, {**payload, "action": "cancel_remove"}).status_code, 200)
        self.assertIsNotNone(Quiz.objects.filter(pk=quiz.id).first())

    def test_published_quiz_post_keeps_submitted_text_without_apply(self):
        self.create_mixed_quiz()
        payload = self.quiz_payload(edits={1: {"prompt": "Unpublished prompt"}})
        self.publish()
        stale = self.client.post(reverse("quiz_editor", args=[self.training.id]), payload)
        self.assertContains(stale, "This version was published", status_code=409)
        self.assertContains(stale, "Unpublished prompt", status_code=409)
        self.assertNotContains(stale, "Apply my quiz changes", status_code=409)

    def test_four_formats_grade_exactly_and_show_only_submitted_answers(self):
        quiz = self.create_four_format_quiz()
        self.assertEqual(self.publish().status_code, 302)
        assignment = self.assign_and_acknowledge()
        questions = {question.type: question for question in quiz.questions.prefetch_related("options")}
        single = questions[Question.Type.SINGLE_CHOICE]
        true_false = questions[Question.Type.TRUE_FALSE]
        multiple = questions[Question.Type.MULTIPLE_SELECTION]
        ordering = questions[Question.Type.ORDERING]
        correct_single = single.options.get(is_correct=True)
        correct_boolean = true_false.options.get(is_correct=True)
        selected_multiple = list(multiple.options.filter(is_correct=True))
        required_order = list(ordering.options.order_by("required_rank"))
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        page = self.client.get(reverse("take_quiz", args=[assignment.id]))
        self.assertContains(page, "Select every answer that applies")
        self.assertContains(page, "Step 1")
        self.assertNotContains(page, "required_rank")
        self.assertNotContains(page, "is_correct")
        self.assertNotContains(page, "rank_")
        submission_id = uuid.uuid4()
        wrong_payload = {
            "submission_id": str(submission_id),
            f"answer_{single.id}": str(correct_single.id),
            f"answer_{true_false.id}": str(correct_boolean.id),
            f"multi_{multiple.id}": [str(selected_multiple[0].id)],
            f"order_{ordering.id}": [str(option.id) for option in ordering.options.all()],
        }
        self.assertEqual(self.client.post(reverse("submit_quiz", args=[assignment.id]), wrong_payload).status_code, 302)
        failed = QuizAttempt.objects.get(submission_id=submission_id)
        self.assertEqual((failed.correct_count, failed.question_count, failed.passed), (2, 4, False))
        self.assertIsNone(Assignment.objects.get(pk=assignment.id).completed_at)
        good_payload = {
            **wrong_payload, "submission_id": str(uuid.uuid4()),
            f"multi_{multiple.id}": [str(option.id) for option in reversed(selected_multiple)],
            f"order_{ordering.id}": [str(option.id) for option in required_order],
        }
        self.assertEqual(self.client.post(reverse("submit_quiz", args=[assignment.id]), good_payload).status_code, 302)
        passed = QuizAttempt.objects.get(submission_id=good_payload["submission_id"])
        self.assertEqual((passed.correct_count, passed.question_count, passed.passed), (4, 4, True))
        replay_payload = {**good_payload, f"multi_{multiple.id}": [str(option.id) for option in selected_multiple]}
        self.assertEqual(self.client.post(reverse("submit_quiz", args=[assignment.id]), replay_payload).status_code, 302)
        self.assertEqual(QuizAttempt.objects.filter(assignment=assignment).count(), 2)
        altered_replay = {**good_payload, f"order_{ordering.id}": [str(option.id) for option in reversed(required_order)]}
        self.assertEqual(self.client.post(reverse("submit_quiz", args=[assignment.id]), altered_replay).status_code, 400)
        self.assertEqual(Assignment.objects.get(pk=assignment.id).completed_at, passed.submitted_at)
        self.assertEqual(AttemptResponseSelection.objects.filter(response__attempt=passed).count(), 5)
        self.assertEqual(
            list(AttemptResponseSelection.objects.filter(
                response__attempt=passed, response__question=ordering,
            ).values_list("option_id", flat=True)),
            [option.id for option in required_order],
        )
        result = self.client.get(reverse("quiz_result", args=[assignment.id, passed.id]))
        self.assertContains(result, "Your selections:")
        self.assertContains(result, "Your order:")
        self.assertNotContains(result, "required rank")
        self.assertNotContains(result, "required_rank")
        self.assertNotContains(result, "is_correct")
        self.assertNotContains(result, "— correct")
        self.assertEqual(QuizAttempt.objects.filter(assignment=assignment).count(), 2)

    def test_malformed_multi_selection_and_ordering_sequences_record_no_attempt(self):
        quiz = self.create_four_format_quiz()
        self.publish()
        assignment = self.assign_and_acknowledge()
        questions = {question.type: question for question in quiz.questions.prefetch_related("options")}
        single = questions[Question.Type.SINGLE_CHOICE]
        true_false = questions[Question.Type.TRUE_FALSE]
        multiple = questions[Question.Type.MULTIPLE_SELECTION]
        ordering = questions[Question.Type.ORDERING]
        multi_options = list(multiple.options.all())
        order_options = list(ordering.options.all())
        valid = {
            single.id: single.options.first().id,
            true_false.id: true_false.options.first().id,
            multiple.id: [multi_options[0].id, multi_options[1].id],
            ordering.id: [option.id for option in order_options],
        }
        invalid = [
            {**valid, multiple.id: []},
            {**valid, multiple.id: [multi_options[0].id, multi_options[0].id]},
            {**valid, multiple.id: [multi_options[0].id, order_options[0].id]},
            {**valid, ordering.id: [option.id for option in order_options[:-1]]},
            {**valid, ordering.id: [order_options[0].id, order_options[0].id, order_options[2].id]},
            {**valid, ordering.id: [order_options[0].id, order_options[1].id, multi_options[0].id]},
            {**valid, ordering.id: [option.id for option in order_options] + [order_options[0].id]},
        ]
        for answers in invalid:
            with self.subTest(answers=answers):
                with self.assertRaises(QuizSubmissionError):
                    submit_quiz(assignment_id=assignment.id, employee=self.employee,
                                submission_id=uuid.uuid4(), answers=answers)
        self.assertEqual(QuizAttempt.objects.count(), 0)
        self.assertEqual(AttemptResponseSelection.objects.count(), 0)
        graded_wrong = submit_quiz(
            assignment_id=assignment.id, employee=self.employee, submission_id=uuid.uuid4(),
            answers={**valid, multiple.id: [option.id for option in multi_options]},
        )
        self.assertFalse(graded_wrong.passed)
        self.assertEqual(graded_wrong.correct_count, 2)

    def test_order_presentation_is_independent_of_required_sequence(self):
        quiz = self.create_four_format_quiz()
        ordering = quiz.questions.get(type=Question.Type.ORDERING)
        fixed_submission = uuid.uuid4()
        before = [option.id for option in presentation_options(ordering, fixed_submission)]
        options = list(ordering.options.all())
        QuestionOption.objects.filter(question=ordering).update(required_rank=None)
        QuestionOption.objects.filter(pk=options[0].id).update(required_rank=1)
        QuestionOption.objects.filter(pk=options[1].id).update(required_rank=2)
        QuestionOption.objects.filter(pk=options[2].id).update(required_rank=3)
        ordering = quiz.questions.get(type=Question.Type.ORDERING)
        after = [option.id for option in presentation_options(ordering, fixed_submission)]
        self.assertEqual(before, after)
        self.assertEqual(set(after), {option.id for option in options})

    def test_invalid_order_keeps_answers_and_token_until_corrected(self):
        quiz = self.create_four_format_quiz()
        self.publish()
        assignment = self.assign_and_acknowledge()
        questions = {question.type: question for question in quiz.questions.prefetch_related("options")}
        single = questions[Question.Type.SINGLE_CHOICE]
        true_false = questions[Question.Type.TRUE_FALSE]
        multiple = questions[Question.Type.MULTIPLE_SELECTION]
        ordering = questions[Question.Type.ORDERING]
        order_options = list(ordering.options.all())
        selected_single = str(single.options.first().id)
        selected_multiple = str(multiple.options.first().id)
        token = str(uuid.uuid4())
        payload = {
            "submission_id": token,
            f"answer_{single.id}": selected_single,
            f"answer_{true_false.id}": str(true_false.options.first().id),
            f"multi_{multiple.id}": [selected_multiple],
            f"order_{ordering.id}": [
                str(order_options[0].id), str(order_options[0].id), str(order_options[2].id),
            ],
        }
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        response = self.client.post(reverse("submit_quiz", args=[assignment.id]), payload)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Choose each option only once.", status_code=400)
        self.assertContains(response, f'href="#question-{ordering.id}"', status_code=400)
        self.assertContains(response, f'value="{token}"', status_code=400)
        rows = {row["id"]: row for row in response.context["questions"]}
        self.assertTrue(next(option for option in rows[single.id]["options"]
                             if option["value"] == selected_single)["selected"])
        self.assertTrue(next(option for option in rows[multiple.id]["options"]
                             if option["value"] == selected_multiple)["selected"])
        self.assertEqual(rows[ordering.id]["positions"][0]["selected"],
                         rows[ordering.id]["positions"][1]["selected"])
        self.assertContains(response, 'role="alert"', status_code=400)
        self.assertNotContains(response, "required_rank", status_code=400)
        self.assertEqual(QuizAttempt.objects.count(), 0)
        payload[f"order_{ordering.id}"] = [str(option.id) for option in order_options]
        accepted = self.client.post(reverse("submit_quiz", args=[assignment.id]), payload)
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(self.client.post(reverse("submit_quiz", args=[assignment.id]), payload).status_code, 302)
        self.assertEqual(QuizAttempt.objects.filter(assignment=assignment).count(), 1)

    def test_missing_and_foreign_answers_return_to_question_without_attempt(self):
        quiz = self.create_four_format_quiz()
        self.publish()
        assignment = self.assign_and_acknowledge()
        questions = {question.type: question for question in quiz.questions.prefetch_related("options")}
        single = questions[Question.Type.SINGLE_CHOICE]
        true_false = questions[Question.Type.TRUE_FALSE]
        multiple = questions[Question.Type.MULTIPLE_SELECTION]
        ordering = questions[Question.Type.ORDERING]
        payload = {
            "submission_id": str(uuid.uuid4()),
            f"answer_{single.id}": str(single.options.first().id),
            f"answer_{true_false.id}": str(true_false.options.first().id),
            f"order_{ordering.id}": [str(option.id) for option in ordering.options.all()],
        }
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        url = reverse("submit_quiz", args=[assignment.id])
        missing = self.client.post(url, payload)
        self.assertContains(missing, f'href="#question-{multiple.id}"', status_code=400)
        self.assertContains(missing, "Choose at least one option.", status_code=400)
        payload[f"multi_{multiple.id}"] = [str(single.options.first().id)]
        foreign = self.client.post(url, payload)
        self.assertContains(foreign, "Choose options from this question only.", status_code=400)
        self.assertEqual(QuizAttempt.objects.count(), 0)

    def test_stale_quiz_submissions_link_to_current_assignment(self):
        quiz = self.create_mixed_quiz()
        self.publish()
        assignment = self.assign_and_acknowledge()
        questions = list(quiz.questions.prefetch_related("options"))
        payload = {"submission_id": str(uuid.uuid4())}
        payload.update({f"answer_{question.id}": str(question.options.first().id) for question in questions})
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.employee.id)})
        url = reverse("submit_quiz", args=[assignment.id])
        accepted = self.client.post(url, payload)
        self.assertEqual(accepted.status_code, 302)
        stale = self.client.post(url, {**payload, "submission_id": str(uuid.uuid4())})
        self.assertContains(stale, "already complete", status_code=400)
        self.assertContains(stale, reverse("assignment_detail", args=[assignment.id]), status_code=400)
        self.assertEqual(QuizAttempt.objects.count(), 1)
        Assignment.objects.filter(pk=assignment.id).update(canceled_at=timezone.now())
        canceled = self.client.post(url, payload)
        self.assertContains(canceled, "was canceled", status_code=403)
        self.assertContains(canceled, reverse("my_training"), status_code=403)
        self.assertEqual(QuizAttempt.objects.count(), 1)

    def test_incomplete_new_question_rules_block_publication(self):
        self.edit_quiz("add_quiz")
        self.edit_quiz("add_multiple")
        self.edit_quiz("add_ordering")
        self.assertEqual(self.edit_quiz(edits={
            1: {"prompt": "Select all", "texts": ["One", "Two"], "correct_indexes": []},
            2: {"prompt": "Put in order", "texts": ["One", "Two", "Three"], "ranks": [1, None, 3]},
        }).status_code, 302)
        response = self.publish()
        self.assertContains(response, "Question 1: select at least one correct option")
        self.assertContains(response, "Question 2: assign every option one required rank")
        self.assertEqual(self.version.status, TrainingVersion.Status.DRAFT)
        self.assertEqual(self.edit_quiz(edits={2: {"ranks": [1, 1, 3]}}).status_code, 400)

    def test_revised_grading_rules_leave_old_assignment_and_score_intact(self):
        quiz = self.create_four_format_quiz()
        self.publish()
        first_version = self.version
        old_assignment = self.assign_and_acknowledge()
        old_questions = {question.type: question for question in quiz.questions.prefetch_related("options")}
        old_answers = {
            old_questions[Question.Type.SINGLE_CHOICE].id: old_questions[Question.Type.SINGLE_CHOICE].options.get(is_correct=True).id,
            old_questions[Question.Type.TRUE_FALSE].id: old_questions[Question.Type.TRUE_FALSE].options.get(is_correct=True).id,
            old_questions[Question.Type.MULTIPLE_SELECTION].id: [
                option.id for option in old_questions[Question.Type.MULTIPLE_SELECTION].options.filter(is_correct=True)
            ],
            old_questions[Question.Type.ORDERING].id: [
                option.id for option in old_questions[Question.Type.ORDERING].options.order_by("required_rank")
            ],
        }
        old_attempt = submit_quiz(assignment_id=old_assignment.id, employee=self.employee,
                                  submission_id=uuid.uuid4(), answers=old_answers)
        self.assertEqual(old_attempt.correct_count, 4)
        self.assertEqual(self.client.post(reverse("new_revision", args=[self.training.id])).status_code, 302)
        self.version = self.training.versions.get(version_number=2)
        self.slide = self.version.slides.get()
        self.assertEqual(self.edit_quiz(edits={
            3: {"correct_indexes": [2]},
            4: {"ranks": [1, 2, 3]},
        }, threshold="75").status_code, 302)
        self.assertEqual(self.publish().status_code, 302)
        self.assertEqual(
            list(first_version.quiz.questions.get(type=Question.Type.ORDERING).options.values_list("required_rank", flat=True)),
            [2, 3, 1],
        )
        self.assertEqual(QuizAttempt.objects.get(pk=old_attempt.id).correct_count, 4)
        self.assertEqual(Assignment.objects.get(pk=old_assignment.id).version_id, first_version.id)
        due_date = (timezone.localdate() + timedelta(days=1)).isoformat()
        create_assignments(operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
                           employee_ids=[self.employee.id], due_date_value=due_date)
        new_assignment = Assignment.objects.get(employee=self.employee, version=self.version)
        acknowledge_slide(assignment_id=new_assignment.id, slide_id=self.slide.id, employee=self.employee)
        new_questions = {question.type: question for question in self.version.quiz.questions.prefetch_related("options")}
        same_visible_choices = {
            new_questions[Question.Type.SINGLE_CHOICE].id: new_questions[Question.Type.SINGLE_CHOICE].options.first().id,
            new_questions[Question.Type.TRUE_FALSE].id: new_questions[Question.Type.TRUE_FALSE].options.first().id,
            new_questions[Question.Type.MULTIPLE_SELECTION].id: [
                option.id for option in list(new_questions[Question.Type.MULTIPLE_SELECTION].options.all())[:2]
            ],
            new_questions[Question.Type.ORDERING].id: [
                option.id for option in [
                    new_questions[Question.Type.ORDERING].options.all()[2],
                    new_questions[Question.Type.ORDERING].options.all()[0],
                    new_questions[Question.Type.ORDERING].options.all()[1],
                ]
            ],
        }
        new_attempt = submit_quiz(assignment_id=new_assignment.id, employee=self.employee,
                                  submission_id=uuid.uuid4(), answers=same_visible_choices)
        self.assertEqual(new_attempt.correct_count, 2)
        self.assertFalse(new_attempt.passed)
        self.assertEqual(QuizAttempt.objects.get(pk=old_attempt.id).correct_count, 4)

    def test_structural_actions_return_focus_to_changed_question(self):
        quiz = self.create_mixed_quiz()
        question = quiz.questions.get(position=1)
        new_question_response = self.edit_quiz("add_ordering")
        new_question = quiz.questions.last()
        self.assertTrue(new_question_response.url.endswith(f"#prompt-{new_question.id}"))
        added = self.edit_quiz(f"add_option:{question.id}")
        option = question.options.last()
        self.assertTrue(added.url.endswith(f"#option-{option.id}"))
        moved = self.edit_quiz(f"down_question:{question.id}")
        self.assertTrue(moved.url.endswith(f"#question-heading-{question.id}"))
        removed = self.edit_quiz(f"remove_option:{option.id}")
        self.assertTrue(removed.url.endswith(f"#prompt-{question.id}"))
        removed_question = self.edit_quiz(f"remove_question:{new_question.id}")
        self.assertTrue(removed_question.url.endswith(f"#question-heading-{question.id}"))

    def test_question_type_change_preserves_options_and_first_correct_answer(self):
        quiz = self.create_four_format_quiz()
        question = quiz.questions.get(type=Question.Type.MULTIPLE_SELECTION)
        original = list(question.options.values_list("id", flat=True))
        payload = self.quiz_payload(action=f"change_type:{question.id}:single_choice", edits={
            3: {"correct_indexes": [0, 1]},
        })
        response = self.client.post(reverse("quiz_editor", args=[self.training.id]), payload)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith(f"#question-heading-{question.id}"))
        question.refresh_from_db()
        self.assertEqual(question.type, Question.Type.SINGLE_CHOICE)
        self.assertEqual(list(question.options.values_list("id", flat=True)), original)
        self.assertEqual(list(question.options.filter(is_correct=True).values_list("id", flat=True)), original[:1])
        changed_back = self.edit_quiz(f"change_type:{question.id}:multiple_selection")
        self.assertEqual(changed_back.status_code, 302)
        question.refresh_from_db()
        self.assertEqual(question.type, Question.Type.MULTIPLE_SELECTION)
        self.assertEqual(list(question.options.values_list("id", flat=True)), original)

    def test_question_type_change_rejects_other_types_and_stale_revisions(self):
        quiz = self.create_four_format_quiz()
        ordering = quiz.questions.get(type=Question.Type.ORDERING)
        payload = self.quiz_payload(action=f"change_type:{ordering.id}:single_choice")
        response = self.client.post(reverse("quiz_editor", args=[self.training.id]), payload)
        self.assertContains(response, "Only single-choice and multiple-selection questions can change type.", status_code=400)
        ordering.refresh_from_db()
        self.assertEqual(ordering.type, Question.Type.ORDERING)
        payload["revision"] = self.version.revision - 1
        self.assertEqual(self.client.post(reverse("quiz_editor", args=[self.training.id]), payload).status_code, 409)

    def test_editor_shows_readiness_and_learner_preview_without_answer_keys(self):
        quiz = self.create_four_format_quiz()
        editor_url = reverse("quiz_editor", args=[self.training.id])
        editor = self.client.get(editor_url)
        self.assertContains(editor, "Ready")
        self.assertContains(editor, "Preview saved quiz as learner")
        self.assertContains(editor, "True is correct")
        self.assertContains(editor, 'data-rank-warning')
        preview_url = reverse("quiz_learner_preview", args=[quiz.id])
        preview = self.client.get(preview_url)
        self.assertContains(preview, "Learner preview")
        self.assertContains(preview, "Select every answer that applies.")
        self.assertNotContains(preview, " — correct")
        self.assertNotContains(preview, "required rank")
        self.admin.role = Employee.Role.EMPLOYEE
        self.admin.save(update_fields=["role"])
        self.assertEqual(self.client.get(preview_url).status_code, 403)

    def test_unfinished_question_is_marked_before_publication_review(self):
        self.edit_quiz("add_quiz")
        self.edit_quiz("add_single")
        editor = self.client.get(reverse("quiz_editor", args=[self.training.id]))
        self.assertContains(editor, "Needs work")
        self.assertContains(editor, "Question 1: add a prompt")
