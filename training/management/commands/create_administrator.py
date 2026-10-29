from getpass import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from training.accounts import normalize_email
from training.models import Employee


class Command(BaseCommand):
    help = "Create the first real administrator. Refuses once any real administrator exists."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--email", required=True)

    def handle(self, *args, name, email, **options):
        name, email = name.strip(), normalize_email(email)
        if not name or not email:
            raise CommandError("Enter a name and an email address.")
        if Employee.objects.filter(role=Employee.Role.ADMINISTRATOR, is_demo=False).exists():
            raise CommandError("A real administrator already exists. Use the Accounts page instead.")
        administrator = Employee(name=name, email=email, role=Employee.Role.ADMINISTRATOR)
        password = getpass("Password: ")
        if password != getpass("Password (again): "):
            raise CommandError("The passwords don't match.")
        try:
            validate_password(password, administrator)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from None
        now = timezone.now()
        administrator.set_password(password)
        # The operator vouches for the address and the approval.
        administrator.verified_email = email
        administrator.email_verified_at = now
        administrator.approval_decided_at = now
        try:
            with transaction.atomic():
                administrator.save()
        except IntegrityError:
            raise CommandError("An employee already uses that email.") from None
        self.stdout.write(self.style.SUCCESS(f"Administrator ready: {email}"))
