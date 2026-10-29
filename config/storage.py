import os

from django.conf import settings
from django.contrib.staticfiles import finders
from whitenoise.storage import CompressedManifestStaticFilesStorage


class VersionedStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Hashed names in deployment; in debug, a ?v= query so browsers fetch edited files."""

    def url(self, name, force=False):
        url = super().url(name, force)
        if settings.DEBUG and not force:
            path = finders.find(name)
            if path:
                url += f"?v={os.stat(path).st_mtime_ns:x}"
        return url
