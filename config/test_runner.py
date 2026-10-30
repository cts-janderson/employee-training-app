from django.core.management import call_command
from django.test.runner import DiscoverRunner


class StaticBuildingRunner(DiscoverRunner):
    """Build static files before the tests, so a fresh checkout passes.

    Django runs tests with DEBUG off, so pages render {% static %} through the manifest that collectstatic writes.
    The build is incremental and also picks up static files added since the last one.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        call_command("collectstatic", interactive=False, verbosity=0)
