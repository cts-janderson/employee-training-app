from django.core.management.base import BaseCommand, CommandError

from training.accounts import normalize_email
from training.models import Employee
from training.passkeys import remove_all


class Command(BaseCommand):
    help = "Remove every passkey an administrator has, for a lost device. Records a security event."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)

    def handle(self, *args, **options):
        employee = Employee.objects.filter(email__iexact=normalize_email(options["email"]), is_demo=False).first()
        if employee is None:
            raise CommandError("No employee uses that email.")
        # Only administrators get passkeys. A former administrator may still hold some to clean up.
        if employee.role != Employee.Role.ADMINISTRATOR and not employee.passkeys.exists():
            raise CommandError("Only administrators have passkeys, and this employee has none.")
        self.stdout.write(f"Removed {remove_all(employee)} passkey(s) for {employee.email}.")
