import uuid
import pickle
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.http import Http404, QueryDict
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .assignments import (
    AssignmentInputError, LearnerDenial, _plan_one, assignment_state, cancel_assignment,
    create_assignments, learner_access, parse_assignment_request, plan_assignments,
)
from .employees import EmployeeInputError, _validated_profile
from .models import (
    Assignment, AssignmentOperationResult, Employee, Question, QuestionOption, Quiz,
    Slide, Training, TrainingVersion,
)
from .question_types import for_type, type_for_add_action
from .quiz_authoring import _locked_library_quiz, read_question_rows, removal_review, save_library_quiz
from .quiz_views import editable_version
from .services import DraftConflict, DraftInputError, Problem, _publication_problems, save_draft


class StructuredProblemTests(SimpleTestCase):
    def test_title_description_slide_and_profile_limits_have_targets(self):
        rows = [{"id": uuid.uuid4(), "header": "Header", "body": "Body", "footer": ""}]
        cases = [
            ("x" * 201, "", rows, ("title",)),
            ("Title", "x" * 5001, rows, ("description",)),
            ("Title", "", [rows[0], {**rows[0], "body": "x" * 10001}], ("slide", 2, "body")),
        ]
        for title, description, slide_rows, target in cases:
            with self.subTest(target=target):
                with self.assertRaises(DraftInputError) as raised:
                    save_draft(version_id=uuid.uuid4(), expected_revision=0, title=title,
                               description=description, rows=slide_rows, action="save")
                self.assertEqual(raised.exception.target, target)
        with self.assertRaises(DraftInputError) as raised:
            save_library_quiz(quiz_id=uuid.uuid4(), expected_revision=0, title="x" * 201,
                              threshold_value="", rows=[], action="save")
        self.assertEqual(raised.exception.target, ("title",))
        for name, email, target in (("", "alice@example.test", ("profile", "name")),
                                    ("Alice", "invalid", ("profile", "email")),
                                    ("Alice", "alice@example.com", ("profile", "email"))):
            with self.subTest(target=target, email=email):
                with self.assertRaises(EmployeeInputError) as raised:
                    _validated_profile(name, email)
                self.assertEqual(raised.exception.target, target)

    def test_publication_targets_and_question_type_metadata(self):
        problems = _publication_problems("", [
            {"header": "Ready", "body": "Text"}, {"header": "", "body": ""},
        ])
        self.assertEqual([(str(problem), problem.target) for problem in problems], [
            ("Add a training title.", ("title",)),
            ("Slide 2: add a header.", ("slide", 2, "header")),
            ("Slide 2: add a body.", ("slide", 2, "body")),
        ])
        self.assertEqual(Problem("same", ("title",)), "same")
        self.assertEqual(pickle.loads(pickle.dumps(Problem("same", ("title",)))).target, ("title",))
        for kind in Question.Type.values:
            question_type = for_type(kind)
            self.assertEqual(type_for_add_action(question_type.add_action), kind)
            self.assertEqual(question_type.fixed_options, kind == Question.Type.TRUE_FALSE)
        self.assertIsNone(type_for_add_action("unknown"))

    def test_ordering_rank_errors_target_the_submitted_option(self):
        question_id = str(uuid.uuid4())
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        post = QueryDict(mutable=True)
        post.setlist("question_id", [question_id])
        post.setlist("question_type", [Question.Type.ORDERING])
        post.setlist("question_prompt", ["Order these"])
        post.setlist(f"option_id_{question_id}", [first, second])
        post.setlist(f"option_text_{question_id}", ["First", "Second"])
        post.setlist(f"rank_{question_id}", ["1", "bad"])
        with self.assertRaises(DraftInputError) as raised:
            read_question_rows(post)
        self.assertEqual(raised.exception.target, ("rank", second))
        post.setlist(f"rank_{question_id}", ["1", "1"])
        with self.assertRaises(DraftInputError) as raised:
            read_question_rows(post)
        self.assertEqual(raised.exception.target, ("rank", second))
        post.setlist(f"option_text_{question_id}", ["First", "x" * 501])
        with self.assertRaises(DraftInputError) as raised:
            read_question_rows(post)
        self.assertEqual(raised.exception.target, ("option", second))

    def test_assignment_request_parser_preserves_input_messages(self):
        with self.assertRaisesMessage(AssignmentInputError, "An assignment identifier is invalid."):
            parse_assignment_request("bad", [uuid.uuid4()])
        for ids in ([], [uuid.uuid4()] * 2, [uuid.uuid4() for _ in range(101)]):
            with self.subTest(count=len(ids)):
                with self.assertRaisesMessage(AssignmentInputError, "Choose one to 100 distinct employees."):
                    parse_assignment_request(uuid.uuid4(), ids)


@override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test",
                   ORG_TIME_ZONE="America/New_York")
class AssignmentPlanAndAccessTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        self.admin = Employee.objects.create(name="Admin", email="demo.admin@example.test",
                                             role=Employee.Role.ADMINISTRATOR, is_demo=True)
        self.alice = Employee.objects.create(id=uuid.UUID(int=2), name="Alice", email="alice@example.test",
                                             is_demo=True)
        self.bob = Employee.objects.create(id=uuid.UUID(int=1), name="Bob", email="bob@example.test",
                                           is_demo=True)
        self.training = Training.objects.create(is_demo=True)
        self.version = TrainingVersion.objects.create(training=self.training, version_number=1, title="Safety")
        self.slide = Slide.objects.create(version=self.version, position=1, header="Read", body="Material")
        self.version.status = TrainingVersion.Status.PUBLISHED
        self.version.published_at = timezone.now()
        self.version.save()
        self.now = timezone.now()
        self.due_date = self.now.astimezone(ZoneInfo("America/New_York")).date() + timedelta(days=5)

    def assign(self, employees=None):
        return create_assignments(
            operation_id=uuid.uuid4(), actor=self.admin, version_id=self.version.id,
            employee_ids=[employee.id for employee in (employees or [self.alice])],
            due_date_value=self.due_date, now=self.now,
        )

    def test_plan_matches_current_state_and_creation_rechecks_after_review(self):
        current = self.assign()[0].assignment
        self.bob.is_active = False
        self.bob.save(update_fields=["is_active"])
        employees = [self.bob, self.alice]
        with self.assertNumQueries(1):
            planned = plan_assignments(self.version, employees, due_date=current.due_date,
                                       zone_name=current.due_timezone, due_at=current.due_at)
        self.assertEqual([row[1] for row in planned], [
            AssignmentOperationResult.Outcome.INACTIVE, AssignmentOperationResult.Outcome.ALREADY_ASSIGNED,
        ])
        self.assertEqual(_plan_one(self.alice, current, self.version, due_date=current.due_date,
                                   zone_name=current.due_timezone, due_at=current.due_at),
                         AssignmentOperationResult.Outcome.ALREADY_ASSIGNED)
        self.assertEqual(_plan_one(self.alice, current, self.version, due_date=current.due_date + timedelta(days=1),
                                   zone_name=current.due_timezone, due_at=current.due_at),
                         AssignmentOperationResult.Outcome.CONFLICT)
        cancel_assignment(assignment_id=current.id, actor=self.admin, reason="Replace", now=self.now)
        self.assertEqual(self.assign()[0].outcome, AssignmentOperationResult.Outcome.CREATED)

    def test_query_states_and_visibility_match_domain(self):
        results = self.assign([self.alice, self.bob])
        pending = next(result.assignment for result in results if result.employee_id == self.alice.id)
        other = next(result.assignment for result in results if result.employee_id == self.bob.id)
        Assignment.objects.filter(pk=other.pk).update(completed_at=self.now)
        for state in ("pending", "completed", "canceled"):
            self.assertEqual(set(Assignment.objects.in_state(state).values_list("id", flat=True)),
                             {assignment.id for assignment in Assignment.objects.all()
                              if assignment_state(assignment) == state})
        self.assertEqual(list(Assignment.objects.visible_to_learner(self.alice)), [pending])
        with self.assertRaises(ValueError):
            Assignment.objects.in_state("bogus")

    def test_learner_access_classifies_missing_foreign_inactive_and_canceled(self):
        assignment = self.assign()[0].assignment
        self.assertEqual(learner_access(assignment.id, self.alice), (assignment, None))
        self.assertEqual(learner_access(assignment.id, self.bob)[1], LearnerDenial.NOT_YOURS)
        self.alice.is_active = False
        self.alice.save(update_fields=["is_active"])
        self.assertEqual(learner_access(assignment.id, self.alice)[1], LearnerDenial.INACTIVE)
        self.alice.is_active = True
        self.alice.save(update_fields=["is_active"])
        cancel_assignment(assignment_id=assignment.id, actor=self.admin, reason="Withdrawn", now=self.now)
        self.assertEqual(learner_access(assignment.id, self.alice)[1], LearnerDenial.CANCELED)
        self.assertEqual(learner_access(assignment.id, self.bob)[1], LearnerDenial.NOT_YOURS)
        self.assertEqual(learner_access(uuid.uuid4(), self.alice)[1], LearnerDenial.MISSING)
        self.assertEqual(learner_access("not-a-uuid", self.alice)[1], LearnerDenial.MISSING)

    def test_learner_views_preserve_foreign_and_canceled_responses(self):
        self.assign([self.alice, self.bob])
        own = Assignment.objects.get(employee=self.alice)
        foreign = Assignment.objects.get(employee=self.bob)
        self.client.post(reverse("demo_switch"), {"employee_id": str(self.alice.id)})
        attempt_id = uuid.uuid4()

        def statuses(assignment):
            return [
                self.client.get(reverse("assignment_detail", args=[assignment.id])).status_code,
                self.client.get(reverse("assignment_slide", args=[assignment.id, self.slide.id])).status_code,
                self.client.post(reverse("acknowledge", args=[assignment.id, self.slide.id])).status_code,
                self.client.get(reverse("take_quiz", args=[assignment.id])).status_code,
                self.client.post(reverse("submit_quiz", args=[assignment.id])).status_code,
                self.client.get(reverse("quiz_result", args=[assignment.id, attempt_id])).status_code,
            ]

        self.assertEqual(statuses(foreign), [404, 404, 404, 404, 403, 404])
        cancel_assignment(assignment_id=own.id, actor=self.admin, reason="Withdrawn", now=self.now)
        self.assertEqual(statuses(own), [404, 404, 404, 409, 403, 404])

    def test_employee_profile_errors_target_name_and_email(self):
        url = reverse("employee_directory")
        for payload, field in (({"name": "", "email": "new@example.test"}, "name"),
                               ({"name": "New", "email": "invalid"}, "email"),
                               ({"name": "Duplicate", "email": self.alice.email}, "email")):
            with self.subTest(field=field, payload=payload):
                response = self.client.post(url, payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.context["error_field"], field)


class QuizEditingSeamTests(TestCase):
    @override_settings(DEBUG=True, DEV_AUTH_STUB=True, DEV_ADMIN_EMAIL="demo.admin@example.test")
    def test_library_quiz_title_error_links_title_field(self):
        self.client.defaults["HTTP_HOST"] = "localhost"
        Employee.objects.create(name="Admin", email="demo.admin@example.test",
                                role=Employee.Role.ADMINISTRATOR, is_demo=True)
        quiz = Quiz.objects.create(is_demo=True)
        response = self.client.post(reverse("library_quiz_editor", args=[quiz.id]), {
            "revision": quiz.revision, "title": "x" * 201, "passing_percent": "50", "action": "save",
        })
        self.assertContains(response, 'href="#quiz-title"', status_code=400)

    def test_editable_version_falls_back_only_for_post(self):
        training = Training.objects.create(is_demo=True)
        draft = TrainingVersion.objects.create(training=training, version_number=1)
        self.assertEqual(editable_version(training.id, for_post=False), draft)
        draft.status = TrainingVersion.Status.PUBLISHED
        draft.published_at = timezone.now()
        draft.save()
        with self.assertRaisesMessage(Http404, "Draft not found."):
            editable_version(training.id, for_post=False)
        self.assertEqual(editable_version(training.id, for_post=True), draft)
        with self.assertRaisesMessage(Http404, "Training version not found."):
            editable_version(uuid.uuid4(), for_post=True)

    def test_removal_review_and_library_lock(self):
        quiz = Quiz.objects.create(is_demo=True, title="Safety")
        question = Question.objects.create(quiz=quiz, position=1, type=Question.Type.SINGLE_CHOICE, prompt="Pick one")
        option = QuestionOption.objects.create(question=question, position=1, text="Yes")
        self.assertEqual(removal_review(quiz, "review_remove_quiz"),
                         {"label": "the entire quiz", "action": "remove_quiz"})
        self.assertIsNone(removal_review(quiz, "review_remove_quiz", allow_quiz_removal=False))
        self.assertEqual(removal_review(quiz, f"review_remove_question:{question.id}"),
                         {"label": "question 1", "action": f"remove_question:{question.id}"})
        self.assertEqual(removal_review(quiz, f"review_remove_option:{option.id}"),
                         {"label": "option 1 in question 1", "action": f"remove_option:{option.id}"})
        self.assertIsNone(removal_review(quiz, "review_remove_option:not-a-uuid"))
        self.assertIsNone(removal_review(quiz, f"review_remove_option:{uuid.uuid4()}"))
        with self.assertRaisesMessage(DraftConflict, "Stale quiz"):
            _locked_library_quiz(quiz.id, quiz.revision + 1, stale_message="Stale quiz")
