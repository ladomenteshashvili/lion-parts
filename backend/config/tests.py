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
    """Run code in a fresh interpreter whose env holds only .env.example values (+ overrides; None unsets)."""
    env = {"PATH": os.environ.get("PATH", ""), **env_example_values(), **(extra_env or {})}
    env = {key: value for key, value in env.items() if value is not None}
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


class DeployEnvGuardTests(SimpleTestCase):
    """Master plan D10: DEPLOY_ENV fails closed; production refuses every demo/debug switch."""

    SAFE_PRODUCTION = {"DEPLOY_ENV": "production", "DEBUG": "False", "ENABLE_DEMO_ENDPOINTS": "False",
                       "SMS_DEMO_MODE": "False", "PARTS_PROVIDER": "amt", "PAYMENT_PROVIDER": "flitt",
                       "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
                       "SECRET_KEY": "k" * 32 + "Qw3rTy-UiOp_AsDf9GhJ", "AMT_VERIFY_SSL": "True",
                       "FRONTEND_BASE_URL": "https://lionparts.ge", "CSRF_TRUSTED_ORIGINS": "https://lionparts.ge",
                       "ALLOWED_HOSTS": "api.lionparts.ge"}

    def boot(self, **env):
        return run_python("import config.settings as s; print(s.DEPLOY_ENV)", env)

    def assert_refused(self, result, *names):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("ImproperlyConfigured", result.stderr)
        for name in names:
            self.assertIn(name, result.stderr)

    def test_clean_production_starts(self):
        result = self.boot(**self.SAFE_PRODUCTION)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "production"), result.stderr)

    def test_unset_means_production(self):
        self.assertEqual(self.boot(**{**self.SAFE_PRODUCTION, "DEPLOY_ENV": None}).stdout.strip(), "production")
        self.assert_refused(self.boot(DEPLOY_ENV=None), "DEBUG")  # .env.example has DEBUG=True

    def test_production_refuses_each_unsafe_switch(self):
        cases = {
            "DEBUG": {"DEBUG": "True"},
            "ENABLE_DEMO_ENDPOINTS": {"ENABLE_DEMO_ENDPOINTS": "True"},
            "SMS_DEMO_MODE": {"SMS_DEMO_MODE": "True"},
            "PARTS_PROVIDER=demo": {"PARTS_PROVIDER": "demo"},
            "PAYMENT_PROVIDER=fake": {"PAYMENT_PROVIDER": "fake"},
            "SECRET_KEY weak": {"SECRET_KEY": "change-me-to-a-long-random-string"},
            "AMT_VERIFY_SSL=False": {"AMT_VERIFY_SSL": "False"},
            "FRONTEND_BASE_URL without https": {"FRONTEND_BASE_URL": "http://lionparts.ge"},
            "CSRF_TRUSTED_ORIGINS without https": {"CSRF_TRUSTED_ORIGINS": "https://lionparts.ge,http://x.ge"},
            "ALLOWED_HOSTS=*": {"ALLOWED_HOSTS": "*"},
            "console": {"EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"},
            "locmem": {"EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
            "dummy": {"EMAIL_BACKEND": "django.core.mail.backends.dummy.EmailBackend"},
            "filebased": {"EMAIL_BACKEND": "django.core.mail.backends.filebased.EmailBackend"},
        }
        result = self.boot(**{**self.SAFE_PRODUCTION, "EMAIL_BACKEND": "nodots"})  # the guard must not crash
        self.assertEqual(result.returncode, 0, result.stderr)
        for name, override in cases.items():
            with self.subTest(name=name):
                self.assert_refused(self.boot(**{**self.SAFE_PRODUCTION, **override}), name)

    def test_production_cookies_https_and_renderers(self):
        """LP-BE-1202 (backend-review F10): the values a production boot really ends up with."""
        code = ("import config.settings as s; print(s.DEBUG, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, "
                "s.SESSION_COOKIE_HTTPONLY, s.SECURE_SSL_REDIRECT, s.SECURE_HSTS_SECONDS, "
                "s.REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'], s.AMT_VERIFY_SSL)")
        result = run_python(code, self.SAFE_PRODUCTION)
        self.assertEqual(result.stdout.strip(), "False True True True True 31536000 "
                                                "['rest_framework.renderers.JSONRenderer'] True", result.stderr)

    def test_staging_keeps_its_demo_setup(self):
        result = self.boot(DEPLOY_ENV="staging", DEBUG="False", ENABLE_DEMO_ENDPOINTS="True", SMS_DEMO_MODE="True",
                           PARTS_PROVIDER="demo", EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "staging"), result.stderr)

    def test_development_and_test_allow_debug_and_demo(self):
        for env in ("development", "test"):
            with self.subTest(env=env):
                result = self.boot(DEPLOY_ENV=env, DEBUG="True", ENABLE_DEMO_ENDPOINTS="True", SMS_DEMO_MODE="True")
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_value_is_refused(self):
        self.assert_refused(self.boot(DEPLOY_ENV="prod"), "DEPLOY_ENV must be one of")
