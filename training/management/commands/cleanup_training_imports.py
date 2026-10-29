import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from training.models import ImportBatch


class Command(BaseCommand):
    help = "Delete expired import reviews and committed results older than seven days; keep created trainings."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Run every minute alongside the local server.")

    def handle(self, *args, **options):
        while True:
            now = timezone.now()
            count, _ = ImportBatch.objects.filter(
                Q(committed_at__isnull=True, expires_at__lte=now)
                | Q(committed_at__lt=now - timedelta(days=7)),
            ).delete()
            if count or not options["loop"]:
                self.stdout.write(f"Removed {count} expired import records.")
            if not options["loop"]:
                return
            try:
                time.sleep(60)
            except KeyboardInterrupt:
                return
