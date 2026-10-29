import os
import re
import tempfile
from pathlib import Path

from django.templatetags.static import static
from django.test import SimpleTestCase, override_settings


class StaticVersionTests(SimpleTestCase):
    @override_settings(DEBUG=True)
    def test_debug_static_urls_carry_a_version_that_changes_with_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory, "app.js")
            asset.write_text("one")
            os.utime(asset, ns=(1_000_000_000, 1_000_000_000))
            with self.settings(STATICFILES_DIRS=[directory]):
                first = static("app.js")
                os.utime(asset, ns=(2_000_000_000, 2_000_000_000))
                second = static("app.js")
        self.assertRegex(first, r"^/static/app\.js\?v=[0-9a-f]+$")
        self.assertNotEqual(first, second)

    @override_settings(DEBUG=True)
    def test_debug_url_for_app_script_is_versioned(self):
        self.assertTrue(re.fullmatch(r"/static/training/quiz-workspace\.js\?v=[0-9a-f]+",
                                     static("training/quiz-workspace.js")))

    @override_settings(DEBUG=True)
    def test_unknown_files_keep_their_plain_url(self):
        self.assertEqual(static("training/missing.js"), "/static/training/missing.js")
