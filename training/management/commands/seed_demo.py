"""A small, repeatable local-only dataset for UI demos and manual testing."""

import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from training.assignments import (
    acknowledge_slide, archive_assignment, cancel_assignment, create_assignments,
    visit_slide, waive_assignment,
)
from training.models import (
    Assignment, AssignmentOperation, Employee, EmployeeActivityChange, EmployeeGroup,
    EmployeeGroupMembership, Notification, Question, QuestionOption, Quiz, Slide,
    SupportingMaterial, Training, TrainingVersion,
)
from training.quizzes import submit_quiz
from training.services import publish_saved_draft


SEED_TRAINING_ID = uuid.UUID("734a10eb-58b2-49fd-b1cb-a30b16e7c643")


class Command(BaseCommand):
    help = "Create a synthetic local dataset for training demos and manual testing."

    def handle(self, *args, **options):
        if not settings.DEBUG or not settings.DEV_AUTH_STUB:
            raise CommandError("Demo seeds require DJANGO_DEBUG=1 and DEV_AUTH_STUB=1.")
        admin_email = settings.DEV_ADMIN_EMAIL.strip().lower()
        if not admin_email.endswith(".test"):
            raise CommandError("Configure a synthetic .test DEV_ADMIN_EMAIL first.")

        with transaction.atomic():
            if Training.objects.filter(pk=SEED_TRAINING_ID).exists():
                self.stdout.write("Demo data already exists; no records changed.")
                return
            admin, created = Employee.objects.get_or_create(
                email__iexact=admin_email,
                defaults={"name": "Demo Administrator", "email": admin_email,
                          "verified_email": admin_email, "role": Employee.Role.ADMINISTRATOR,
                          "is_demo": True},
            )
            if not created and (not admin.is_demo or not admin.is_active
                                or admin.role != Employee.Role.ADMINISTRATOR):
                raise CommandError("The configured administrator is not an active demo administrator.")
            if admin.verified_email.lower() != admin_email:
                admin.verified_email = admin_email
                admin.save(update_fields=["verified_email"])
            if created:
                Employee.objects.filter(pk=admin.pk).update(created_at=timezone.now() - timedelta(days=260))

            people = self._employees()
            self._groups(people)
            safety = self._safety_training()
            handling = self._handling_training()
            self._draft_training()
            self._library_quiz()
            self._assignments(admin, people, safety, handling)

        self.stdout.write(self.style.SUCCESS(
            "Demo data ready: 6 learners, 2 groups, 2 published trainings, "
            "1 draft, 1 library quiz, and 11 assignments."
        ))

    def _employees(self):
        people = {}
        for key, name in (
            ("maya", "Maya Chen"), ("leo", "Leo Martin"),
            ("priya", "Priya Shah"), ("omar", "Omar Rivera"),
            ("casey", "Casey Brooks"), ("rowan", "Rowan Ellis"),
        ):
            email = f"demo.{key}@example.test"
            if Employee.objects.filter(email__iexact=email).exists():
                raise CommandError(f"{email} already exists; choose a fresh demo database.")
            person = Employee.objects.create(name=name, email=email, verified_email=email,
                                             is_demo=True)
            Employee.objects.filter(pk=person.pk).update(created_at=timezone.now() - timedelta(days=260))
            people[key] = person
        return people

    def _groups(self, people):
        now = timezone.now()
        for name, keys in (
            ("Demo Operations", ("maya", "leo", "priya", "omar")),
            ("Demo New Starters", ("casey", "rowan")),
        ):
            if EmployeeGroup.objects.filter(name__iexact=name).exists():
                raise CommandError(f"{name} already exists; choose a fresh demo database.")
            group = EmployeeGroup.objects.create(name=name, created_at=now)
            for key in keys:
                EmployeeGroupMembership.objects.get_or_create(
                    group=group, employee=people[key], defaults={"added_at": now},
                )

    def _new_version(self, title, description, slides, *, training_id=None, renewal_months=None):
        training = Training.objects.create(id=training_id or uuid.uuid4(),
                                           renewal_interval_months=renewal_months)
        version = TrainingVersion.objects.create(
            training=training, version_number=1, title=title, description=description,
        )
        for position, (header, body) in enumerate(slides, 1):
            Slide.objects.create(version=version, position=position, header=header, body=body)
        return version

    def _question(self, quiz, position, kind, prompt, options):
        question = Question.objects.create(quiz=quiz, position=position, type=kind, prompt=prompt)
        for option_position, (label, correct, rank) in enumerate(options, 1):
            QuestionOption.objects.create(
                question=question, position=option_position, text=label,
                is_correct=correct, required_rank=rank,
            )
        return question

    def _safety_training(self):
        version = self._new_version(
            "Demo: Workplace Safety Essentials",
            "Practice reporting a hazard and responding to an incident.",
            (("Spot a hazard", "Notice blocked exits, spills, and damaged equipment."),
             ("Report promptly", "Tell the designated supervisor and describe the location."),
             ("Follow up", "Keep clear of the hazard and follow the local response procedure.")),
            training_id=SEED_TRAINING_ID,
        )
        SupportingMaterial.objects.create(
            version=version, position=1, name="Example reference link",
            url="https://example.com", description="Placeholder link for the supporting material demo.",
        )
        quiz = Quiz.objects.create(version=version, title="Safety check", passing_percent=75)
        self._question(quiz, 1, Question.Type.SINGLE_CHOICE, "What should you report?", (
            ("A blocked exit", True, None), ("Only injuries", False, None),
        ))
        self._question(quiz, 2, Question.Type.TRUE_FALSE,
                       "A spill should be reported promptly.", (
                           ("True", True, None), ("False", False, None),
                       ))
        self._question(quiz, 3, Question.Type.MULTIPLE_SELECTION,
                       "Which details help with a hazard report?", (
                           ("Location", True, None), ("What you observed", True, None),
                           ("A guess about who is at fault", False, None),
                       ))
        self._question(quiz, 4, Question.Type.ORDERING,
                       "Put the response steps in order.", (
                           ("Follow the local procedure", False, 3),
                           ("Move away from immediate danger", False, 1),
                           ("Tell the designated supervisor", False, 2),
                       ))
        return self._publish_historical(version)

    def _handling_training(self):
        version = self._new_version(
            "Demo: Data Handling Basics",
            "A short slide-only course with a six-month renewal interval.",
            (("Use approved storage", "Keep work files in the approved shared location."),
             ("Check recipients", "Check addresses before sharing a work file.")),
            renewal_months=6,
        )
        return self._publish_historical(version)

    def _publish_historical(self, version):
        published = publish_saved_draft(version_id=version.id, expected_revision=version.revision)
        earlier = timezone.now() - timedelta(days=250)
        Training.objects.filter(pk=version.training_id).update(created_at=earlier)
        TrainingVersion.objects.filter(pk=version.pk).update(created_at=earlier,
                                                               published_at=earlier + timedelta(days=1))
        published.published_at = earlier + timedelta(days=1)
        return published

    def _draft_training(self):
        self._new_version(
            "Demo: Incident Response Update", "Unpublished draft for the authoring demo.",
            (("First response", "Add the team's local response steps here."),),
        )

    def _library_quiz(self):
        quiz = Quiz.objects.create(title="Demo: Privacy refresher", passing_percent=100)
        self._question(quiz, 1, Question.Type.TRUE_FALSE,
                       "Check a recipient before sharing a work file.", (
                           ("True", True, None), ("False", False, None),
                       ))

    def _assignments(self, admin, people, safety, handling):
        now = timezone.now()
        today = now.astimezone(ZoneInfo(settings.ORG_TIME_ZONE)).date()

        def assign(version, key, due_offset, assigned_offset):
            at = now + timedelta(days=assigned_offset)
            result = create_assignments(
                operation_id=uuid.uuid4(), actor=admin, version_id=version.id,
                employee_ids=[people[key].id], due_date_value=today + timedelta(days=due_offset),
                now=at,
            )[0]
            assignment = result.assignment
            Assignment.objects.filter(pk=assignment.pk).update(assigned_at=at)
            AssignmentOperation.objects.filter(pk=result.operation_id).update(created_at=at)
            Notification.objects.filter(assignment=assignment).update(created_at=at)
            assignment.assigned_at = at
            return assignment

        assign(safety, "maya", 14, -7)       # Not started.
        leo = assign(safety, "leo", 7, -7)   # One slide acknowledged.
        visit_slide(assignment_id=leo.id, slide_id=safety.slides.first().id,
                    employee=people["leo"], now=now - timedelta(days=2))
        acknowledge_slide(assignment_id=leo.id, slide_id=safety.slides.first().id,
                          employee=people["leo"], now=now - timedelta(days=2))

        priya = assign(safety, "priya", 5, -8)  # Failed quiz, ready to retry.
        for slide in safety.slides.all():
            acknowledge_slide(assignment_id=priya.id, slide_id=slide.id,
                              employee=people["priya"], now=now - timedelta(days=1))
        wrong_answers = {}
        for question in safety.quiz.questions.prefetch_related("options"):
            options = list(question.options.all())
            if question.type == Question.Type.MULTIPLE_SELECTION:
                wrong_answers[question.id] = [options[-1].id]
            elif question.type == Question.Type.ORDERING:
                wrong_answers[question.id] = [option.id for option in options]
            else:
                wrong_answers[question.id] = next(option.id for option in options if not option.is_correct)
        submit_quiz(assignment_id=priya.id, employee=people["priya"],
                    submission_id=uuid.uuid4(), answers=wrong_answers, now=now - timedelta(days=1))

        omar = assign(safety, "omar", -10, -25)  # Passed before the deadline.
        for slide in safety.slides.all():
            acknowledge_slide(assignment_id=omar.id, slide_id=slide.id,
                              employee=people["omar"], now=now - timedelta(days=12))
        correct_answers = {}
        for question in safety.quiz.questions.prefetch_related("options"):
            options = list(question.options.all())
            if question.type == Question.Type.MULTIPLE_SELECTION:
                correct_answers[question.id] = [option.id for option in options if option.is_correct]
            elif question.type == Question.Type.ORDERING:
                correct_answers[question.id] = [option.id for option in sorted(
                    options, key=lambda option: option.required_rank)]
            else:
                correct_answers[question.id] = next(option.id for option in options if option.is_correct)
        submit_quiz(assignment_id=omar.id, employee=people["omar"],
                    submission_id=uuid.uuid4(), answers=correct_answers, now=now - timedelta(days=12))

        assign(safety, "casey", -2, -14)   # Overdue and not started.
        assign(safety, "rowan", 10, -5)   # Inactive learner's pending work.
        people["rowan"].is_active = False
        people["rowan"].save(update_fields=["is_active"])
        EmployeeActivityChange.objects.create(
            employee=people["rowan"], actor=admin, was_active=True, is_active=False,
            changed_at=now - timedelta(days=1),
        )

        maya = assign(handling, "maya", -8, -22)  # Completed late.
        for slide in handling.slides.all():
            acknowledge_slide(assignment_id=maya.id, slide_id=slide.id,
                              employee=people["maya"], now=now - timedelta(days=6))
        leo = assign(handling, "leo", -198, -210)  # Completed on time; renewal is due.
        for slide in handling.slides.all():
            acknowledge_slide(assignment_id=leo.id, slide_id=slide.id,
                              employee=people["leo"], now=now - timedelta(days=200))
        priya = assign(handling, "priya", 8, -5)
        waive_assignment(assignment_id=priya.id, actor=admin,
                         reason="Demo waiver for a role-specific exemption.", now=now - timedelta(days=2))
        omar = assign(handling, "omar", 9, -5)
        cancel_assignment(assignment_id=omar.id, actor=admin,
                          reason="Demo cancellation after a training plan change.", now=now - timedelta(days=1))
        casey = assign(handling, "casey", 10, -5)
        archive_assignment(assignment_id=casey.id, actor=admin,
                           reason="Demo archived assignment for history review.", now=now - timedelta(days=1))
