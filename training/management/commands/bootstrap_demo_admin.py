from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from training.models import Employee


class Command(BaseCommand):
    help = "Create a synthetic administrator for loopback-only development."

    def handle(self, *args, **options):
        if not settings.DEBUG or not settings.DEV_AUTH_STUB:
            raise CommandError("The demo administrator requires DJANGO_DEBUG=1 and DEV_AUTH_STUB=1.")
        email = settings.DEV_ADMIN_EMAIL.strip().lower()
        if not email.endswith(".test"):
            raise CommandError("Use a synthetic .test email address for the demo administrator.")
        employee, created = Employee.objects.get_or_create(
            email=email,
            defaults={"name": "Demo Administrator", "role": Employee.Role.ADMINISTRATOR,
                      "is_demo": True, "verified_email": email},
        )
        if not created and (not employee.is_demo or not employee.is_active or employee.role != Employee.Role.ADMINISTRATOR):
            raise CommandError("The configured email belongs to an ineligible employee record.")
        if employee.verified_email != email:
            employee.verified_email = email
            employee.save(update_fields=["verified_email"])
        self.stdout.write(self.style.SUCCESS(f"Demo administrator ready: {email}"))
