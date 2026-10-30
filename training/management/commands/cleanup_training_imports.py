import time
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from training.accounts import expire_registrations, prune_rate_limits
from training.passkeys import prune_challenges
from training.models import ImportBatch

SESSION_INTERVAL_SECONDS = 60 * 60


class Command(BaseCommand):
    help = ("Delete expired import reviews and committed results older than seven days; keep created trainings. "
            "Also delete ended rate-limit counters, expired passkey challenges, expired unconfirmed registrations "
            "and, every hour, expired sessions.")

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Run every minute alongside the local server.")

    def handle(self, *args, **options):
        sessions_cleared_at = None
        while True:
            now = timezone.now()
            if sessions_cleared_at is None or time.monotonic() - sessions_cleared_at >= SESSION_INTERVAL_SECONDS:
                call_command("clearsessions")
                sessions_cleared_at = time.monotonic()
            count, _ = ImportBatch.objects.filter(
                Q(committed_at__isnull=True, expires_at__lte=now)
                | Q(committed_at__lt=now - timedelta(days=7)),
            ).delete()
            counters = prune_rate_limits(now=now)
            prune_challenges(now=now)
            registrations = expire_registrations(now=now)
            if count or counters or registrations or not options["loop"]:
                self.stdout.write(f"Removed {count} expired import records, {counters} rate-limit counters and "
                                  f"{registrations} expired registrations.")
            if not options["loop"]:
                return
            try:
                time.sleep(60)
            except KeyboardInterrupt:
                return
