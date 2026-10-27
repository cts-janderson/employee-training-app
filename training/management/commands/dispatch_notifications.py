import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from training.notifications import process_ready_notifications


class Command(BaseCommand):
    help = "Dispatch due notification intents to the loopback development email sink."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Keep polling for due notifications.")
        parser.add_argument("--interval", type=float, default=5, help="Polling interval in seconds with --loop.")
        parser.add_argument("--limit", type=int, default=100, help="Maximum notifications per polling cycle.")

    def handle(self, *args, **options):
        if (not settings.DEBUG or not settings.DEV_AUTH_STUB
                or settings.EMAIL_BACKEND != "django.core.mail.backends.locmem.EmailBackend"):
            raise CommandError("The local notification worker requires debug mode, the demo stub, and the local email sink.")
        if options["interval"] <= 0 or options["limit"] < 1:
            raise CommandError("Use a positive interval and limit.")
        try:
            while True:
                processed = process_ready_notifications(limit=options["limit"])
                self.stdout.write(f"Processed {len(processed)} notification(s).")
                if not options["loop"]:
                    break
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("Notification worker stopped.")
