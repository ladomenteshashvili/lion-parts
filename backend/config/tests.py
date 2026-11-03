"""LP-BE-101 (LPK-96): settings & configuration baseline."""

import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase, override_settings
from django.urls import clear_url_caches

BACKEND_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = BACKEND_DIR / "config" / "settings.py"
ENV_EXAMPLE = BACKEND_DIR / ".env.example"


def env_example_values():
    """Uncommented KEY=VALUE pairs from .env.example."""
    values = {}
    for line in ENV_EXAMPLE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def run_python(code, extra_env=None):
    """Run code in a fresh interpreter whose env holds only .env.example values (+ overrides)."""
    env = {"PATH": os.environ.get("PATH", ""), **env_example_values(), **(extra_env or {})}
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    return subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND_DIR, env=env,
        capture_output=True, text=True, timeout=120,
    )


class EnvExampleTests(SimpleTestCase):
    def test_env_example_lists_every_variable_settings_reads(self):
        read = set(re.findall(r"""config\(\s*["']([A-Z0-9_]+)["']""", SETTINGS_FILE.read_text()))
        listed = set(re.findall(r"^#?\s*([A-Z0-9_]+)=", ENV_EXAMPLE.read_text(), re.MULTILINE))
        self.assertTrue(read)
        self.assertEqual(read - listed, set())

    def test_check_passes_with_only_env_example_variables(self):
        # `check` does not open a DB connection.
        result = run_python(
            "import sys, django; from django.core.management import call_command;"
            "django.setup(); call_command('check'); sys.exit(0)"
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class SecureDefaultsTests(SimpleTestCase):
    PROBE = (
        "import config.settings as s;"
        "print(s.SECURE_SSL_REDIRECT, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE)"
    )

    def probe(self, **env):
        result = run_python(self.PROBE, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split()

    def test_debug_false_enables_https_redirect_and_secure_cookies(self):
        self.assertEqual(self.probe(DEBUG="False"), ["True", "True", "True"])

    def test_debug_true_disables_https_redirect(self):
        self.assertEqual(self.probe(DEBUG="True"), ["False", "False", "False"])

    def test_explicit_override_wins(self):
        self.assertEqual(self.probe(DEBUG="False", SECURE_SSL_REDIRECT="False")[0], "False")

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_http_request_redirects_to_https(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 301)
        self.assertTrue(response["Location"].startswith("https://"))

    def test_https_request_via_proxy_header_is_not_redirected(self):
        with self.settings(SECURE_SSL_REDIRECT=True):
            response = self.client.get("/api/health/", headers={"x-forwarded-proto": "https"})
        self.assertEqual(response.status_code, 200)


class BlankEnvValueTests(SimpleTestCase):
    PROBE = (
        "import config.settings as s;"
        "print(repr(s.SESSION_COOKIE_DOMAIN), repr(s.CSRF_COOKIE_DOMAIN), s.MEDIA_ROOT == str(s.BASE_DIR / 'media'))"
    )

    def probe(self, **env):
        result = run_python(self.PROBE, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split()

    def test_blank_values_mean_unset(self):
        blank = {"SESSION_COOKIE_DOMAIN": "", "CSRF_COOKIE_DOMAIN": "", "MEDIA_ROOT": ""}
        self.assertEqual(self.probe(**blank), ["None", "None", "True"])

    def test_real_values_are_kept(self):
        out = self.probe(SESSION_COOKIE_DOMAIN=".lionparts.ge", CSRF_COOKIE_DOMAIN=".lionparts.ge",
                         MEDIA_ROOT="/srv/lionparts/media")
        self.assertEqual(out, ["'.lionparts.ge'", "'.lionparts.ge'", "False"])


class AdminUrlPathTests(SimpleTestCase):
    def reload_urls(self):
        import config.urls
        clear_url_caches()
        importlib.reload(config.urls)

    def tearDown(self):
        self.reload_urls()  # back to the real ADMIN_URL_PATH

    def test_admin_only_at_admin_url_path(self):
        with self.settings(ADMIN_URL_PATH="ops-test/"):
            self.reload_urls()
            self.assertEqual(self.client.get("/ops-test/").status_code, 302)
            self.assertEqual(self.client.get("/admin/").status_code, 404)
