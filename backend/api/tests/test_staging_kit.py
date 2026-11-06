"""LPK-195-200: the staging scripts refuse every target but the isolated staging, before they run anything that changes a thing."""

import getpass
import os
import stat
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

KIT = Path(settings.BASE_DIR).parent / "deploy" / "staging"
GOOD = {"DEPLOY_ENV": "staging", "DEBUG": "False", "PARTS_PROVIDER": "demo", "PAYMENT_PROVIDER": "fake", "SMS_DEMO_MODE": "True",
        "SENDER_GE_ENABLED": "False", "ENABLE_DEMO_ENDPOINTS": "True", "DB_NAME": "lp_stage_v1", "DB_USER": "lp_stage",
        "DB_HOST": "127.0.0.1", "DB_PORT": "5", "DB_PASSWORD": "x", "EMAIL_HOST": "127.0.0.1", "SECRET_KEY": "x"}


class StageScriptTests(SimpleTestCase):
    def run_stage(self, *args, mode=0o600, user=None, **changes):
        with tempfile.TemporaryDirectory() as home:
            backend = Path(home) / "lion-parts" / "backend"
            backend.mkdir(parents=True)
            env_file = backend / ".env"
            env_file.write_text("".join(f"{key}={value}\n" for key, value in {**GOOD, **changes}.items()))
            env_file.chmod(mode)
            env = {"PATH": os.environ["PATH"], "STAGE_USER": user or getpass.getuser(), "STAGE_HOME": home,
                   "STAGE_CHECKOUT": str(backend.parent), "STAGE_ENV_FILE": str(env_file)}
            return subprocess.run(["bash", str(KIT / "stage.sh"), *args], env=env, capture_output=True, text=True, timeout=20)

    def assert_refused(self, result, reason):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(f"REFUSED: {reason}", result.stderr)

    def test_any_other_database_is_refused(self):
        for name in ("lion_parts", "lion_parts_v2", "lp_stage", "postgres"):
            with self.subTest(name=name):
                self.assert_refused(self.run_stage("check", DB_NAME=name), "DB_NAME must be 'lp_stage_v1'")

    def test_settings_that_are_not_stagings_are_refused(self):
        for key, value in (("DEPLOY_ENV", "production"), ("DEBUG", "True"), ("PARTS_PROVIDER", "amt"),
                           ("PAYMENT_PROVIDER", "flitt"), ("SENDER_GE_ENABLED", "True"), ("SMS_DEMO_MODE", "False"),
                           ("EMAIL_HOST", "smtp.example.com")):
            with self.subTest(key=key):
                self.assert_refused(self.run_stage("backup", **{key: value}), f"{key} must be")

    def test_real_credentials_are_refused(self):
        for key in ("AMT_API_PASSWORD", "SENDER_GE_API_KEY", "FLITT_SECRET_KEY", "EMAIL_HOST_PASSWORD"):
            with self.subTest(key=key):
                self.assert_refused(self.run_stage("check", **{key: "something"}), f"{key} must be empty")

    def test_an_application_role_is_refused(self):
        for role in ("lion_parts", "lion_parts_dev", "postgres"):
            with self.subTest(role=role):
                self.assert_refused(self.run_stage("check", DB_USER=role), f"DB_USER '{role}'")

    def test_another_user_and_a_readable_env_file_are_refused(self):
        self.assert_refused(self.run_stage("check", user="someone-else"), "run as someone-else")
        self.assertIn("mode 600", self.run_stage("check", mode=0o644).stderr)

    def test_replacing_the_database_needs_the_explicit_confirmation(self):
        self.assert_refused(self.run_stage("restore", "/nonexistent"), "set CONFIRM=lp_stage_v1")

    def test_an_unknown_command_does_nothing(self):
        self.assertEqual(self.run_stage("drop").returncode, 2)


class KitFileTests(SimpleTestCase):
    def test_scripts_are_executable(self):
        for name in ("stage.sh", "provision.sh"):
            self.assertTrue((KIT / name).stat().st_mode & stat.S_IXUSR, name)

    def test_provision_without_apply_changes_nothing_and_says_so(self):
        result = subprocess.run(["bash", str(KIT / "provision.sh")], capture_output=True, text=True, timeout=20,
                                env={"PATH": os.environ["PATH"]})
        self.assertIn("Would create, and nothing else", result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_templates_name_only_the_isolated_resources(self):
        text = "".join((KIT / name).read_text() for name in ("env.staging.example", "gunicorn.conf.py", "lionparts-stage.service",
                                                             "lionparts-stage-mailpit.service", "crontab.example"))
        self.assertIn("127.0.0.1:8011", text)
        self.assertIn("DB_NAME=lp_stage_v1", text)
        for forbidden in ("lion_parts", "/home/lado", "/srv/", "PARTS_PROVIDER=amt"):
            self.assertNotIn(forbidden, text)

    def test_nginx_names_cannot_collide_with_other_sites(self):
        text = (KIT / "nginx-stage.conf.template").read_text()
        self.assertIn('"~^', text)  # an unquoted { } in a map regex fails nginx -t
        for line in text.splitlines():
            if line.startswith(("log_format", "limit_req_zone", "map ")):
                self.assertIn("lp_stage_", line)

    def test_the_example_env_file_passes_the_scripts_own_rules(self):
        lines = (KIT / "env.staging.example").read_text().splitlines()
        values = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
        for key, value in GOOD.items():
            if key not in ("DB_PORT", "DB_PASSWORD", "SECRET_KEY"):
                self.assertEqual(values[key], value, key)
