import uuid

from django.test import SimpleTestCase

from .models import Question, QuestionOption
from .question_types import for_type
from .quizzes import QuizSubmissionError


class QuestionTypeTests(SimpleTestCase):
    def option(self, text, *, correct=False, rank=None):
        return QuestionOption(id=uuid.uuid4(), text=text, is_correct=correct, required_rank=rank)

    def test_grade_correct_and_incorrect_answers_for_each_type(self):
        first = self.option("First", correct=True, rank=1)
        second = self.option("Second", rank=2)
        cases = [
            (Question.Type.SINGLE_CHOICE, first.id, second.id, [first.id], [second.id]),
            (Question.Type.TRUE_FALSE, first.id, second.id, [first.id], [second.id]),
            (Question.Type.MULTIPLE_SELECTION, [first.id], [second.id], [first.id], [second.id]),
            (Question.Type.ORDERING, [first.id, second.id], [second.id, first.id],
             [first.id, second.id], [second.id, first.id]),
        ]
        for kind, correct_answer, wrong_answer, correct_ids, wrong_ids in cases:
            with self.subTest(kind=kind):
                question_type = for_type(kind)
                self.assertEqual(question_type.grade([first, second], correct_answer), (correct_ids, True))
                self.assertEqual(question_type.grade([first, second], wrong_answer), (wrong_ids, False))

    def test_grade_rejects_invalid_answers_with_existing_messages(self):
        first = self.option("First", correct=True, rank=1)
        second = self.option("Second", rank=2)
        foreign_id = uuid.uuid4()
        cases = [
            (Question.Type.SINGLE_CHOICE, [first.id], "Choose one valid answer for each single-choice question."),
            (Question.Type.TRUE_FALSE, foreign_id, "Choose one valid answer for each single-choice question."),
            (Question.Type.MULTIPLE_SELECTION, [],
             "Choose distinct valid options for each multiple-selection question."),
            (Question.Type.MULTIPLE_SELECTION, [first.id, first.id],
             "Choose distinct valid options for each multiple-selection question."),
            (Question.Type.ORDERING, [first.id], "Order every option exactly once."),
            (Question.Type.ORDERING, [first.id, first.id], "Order every option exactly once."),
        ]
        for kind, answer, message in cases:
            with self.subTest(kind=kind, answer=answer):
                with self.assertRaisesMessage(QuizSubmissionError, message):
                    for_type(kind).grade([first, second], answer)
        invalid_ranks = [self.option("First", rank=1), self.option("Second", rank=1)]
        with self.assertRaisesMessage(QuizSubmissionError, "This ordering question has invalid grading rules."):
            for_type(Question.Type.ORDERING).grade(invalid_ranks, [option.id for option in invalid_ranks])

    def test_answer_problem_preserves_each_learner_message(self):
        valid = {"first", "second"}
        cases = [
            (Question.Type.SINGLE_CHOICE, [], "Choose one answer."),
            (Question.Type.SINGLE_CHOICE, ["foreign"], "Choose an answer from this question."),
            (Question.Type.TRUE_FALSE, [""], "Choose one answer."),
            (Question.Type.TRUE_FALSE, ["foreign"], "Choose an answer from this question."),
            (Question.Type.MULTIPLE_SELECTION, [], "Choose at least one option."),
            (Question.Type.MULTIPLE_SELECTION, ["foreign"], "Choose options from this question only."),
            (Question.Type.MULTIPLE_SELECTION, ["first", "first"], "Choose each option only once."),
            (Question.Type.ORDERING, ["first"], "Choose an option for every position."),
            (Question.Type.ORDERING, ["first", "foreign"], "Choose options from this question only."),
            (Question.Type.ORDERING, ["first", "first"], "Choose each option only once."),
        ]
        for kind, values, message in cases:
            with self.subTest(kind=kind, values=values):
                self.assertEqual(for_type(kind).answer_problem(values, valid), message)
        for kind in Question.Type.values:
            with self.subTest(kind=kind, valid=True):
                values = ["first", "second"] if kind == Question.Type.ORDERING else ["first"]
                self.assertIsNone(for_type(kind).answer_problem(values, valid))

    def test_content_problems_preserves_type_specific_messages_and_order(self):
        single = [self.option("First", correct=True), self.option("Second")]
        true_false = [self.option("True", correct=True), self.option("False")]
        multiple = [self.option("First", correct=True), self.option("Second")]
        ordering = [self.option("First", rank=1), self.option("Second", rank=2)]
        valid_cases = [
            (Question.Type.SINGLE_CHOICE, single), (Question.Type.TRUE_FALSE, true_false),
            (Question.Type.MULTIPLE_SELECTION, multiple), (Question.Type.ORDERING, ordering),
        ]
        for kind, options in valid_cases:
            with self.subTest(kind=kind, valid=True):
                self.assertEqual(for_type(kind).content_problems(2, options), [])

        cases = [
            (Question.Type.SINGLE_CHOICE, [self.option("", correct=True)], [
                "Question 2: add at least two filled options.",
            ]),
            (Question.Type.SINGLE_CHOICE, [self.option("First"), self.option("Second")], [
                "Question 2: select exactly one correct answer.",
            ]),
            (Question.Type.TRUE_FALSE, [self.option("Yes", correct=True), self.option("No")], [
                "Question 2: true or false needs True and False options.",
            ]),
            (Question.Type.TRUE_FALSE, [self.option("True"), self.option("False")], [
                "Question 2: select exactly one correct answer.",
            ]),
            (Question.Type.MULTIPLE_SELECTION, [self.option(""), self.option("Second")], [
                "Question 2: add at least two filled options.",
                "Question 2: select at least one correct option.",
            ]),
            (Question.Type.ORDERING, [self.option("First", rank=1), self.option("Second")], [
                "Question 2: assign every option one required rank from 1 to 2.",
            ]),
            (Question.Type.ORDERING, [self.option("", rank=1), self.option("Second", rank=2)], [
                "Question 2: add at least two filled options.",
            ]),
            (Question.Type.ORDERING, [self.option("First", rank=1, correct=True), self.option("Second", rank=2)], [
                "Question 2: ordering uses required ranks, not correct-option flags.",
            ]),
        ]
        for kind, options, messages in cases:
            with self.subTest(kind=kind, messages=messages):
                self.assertEqual(for_type(kind).content_problems(2, options), messages)

    def test_for_type_rejects_unknown_kind(self):
        with self.assertRaises(KeyError):
            for_type("unknown")
