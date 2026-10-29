import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from training.models import WorkerHeartbeat
from training.notifications import process_ready_notifications

LOCAL_SINK = "django.core.mail.backends.locmem.EmailBackend"


class Command(BaseCommand):
    help = "Dispatch due notification intents through the configured email transport."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Keep polling for due notifications.")
        parser.add_argument("--interval", type=float, default=5, help="Polling interval in seconds with --loop.")
        parser.add_argument("--limit", type=int, default=100, help="Maximum notifications per polling cycle.")

    def handle(self, *args, **options):
        graph = settings.EMAIL_BACKEND == settings.GRAPH_EMAIL_BACKEND
        local = settings.DEBUG and settings.DEV_AUTH_STUB and settings.EMAIL_BACKEND == LOCAL_SINK
        if not graph and not local:
            raise CommandError("Configure Microsoft Graph email, or use debug mode with the demo stub and the "
                               "local email sink.")
        if options["interval"] <= 0 or options["limit"] < 1:
            raise CommandError("Use a positive interval and limit.")
        stopping = []

        def stop(signum, frame):
            # Finish the notification in hand, then exit before claiming another.
            stopping.append(signum)

        previous = {number: signal.signal(number, stop) for number in (signal.SIGTERM, signal.SIGINT)}
        try:
            self._run(options, stopping)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
        if stopping:
            self.stdout.write("Notification worker stopped.")

    def _run(self, options, stopping):
        while not stopping:
            processed = process_ready_notifications(limit=options["limit"], should_stop=lambda: bool(stopping))
            WorkerHeartbeat.objects.update_or_create(name="notifications", defaults={"beat_at": timezone.now()})
            self.stdout.write(f"Processed {len(processed)} notification(s).")
            if not options["loop"]:
                break
            deadline = time.monotonic() + options["interval"]
            while not stopping and time.monotonic() < deadline:
                time.sleep(min(0.5, options["interval"]))
