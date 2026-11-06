"""LPK-195-200: the staging scripts refuse every target but the isolated staging, before they run anything that changes a thing."""

import getpass
import importlib.util
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TestCase

KIT = Path(settings.BASE_DIR).parent / "deploy" / "staging"
GOOD = {"DEPLOY_ENV": "staging", "DEBUG": "False", "PARTS_PROVIDER": "demo", "PAYMENT_PROVIDER": "fake", "SMS_DEMO_MODE": "True",
        "SENDER_GE_ENABLED": "False", "ENABLE_DEMO_ENDPOINTS": "True", "DB_NAME": "lp_stage_v1", "DB_USER": "lp_stage",
        "DB_HOST": "127.0.0.1", "DB_PORT": "5432", "DB_PASSWORD": "x", "EMAIL_HOST": "127.0.0.1", "SECRET_KEY": "x"}
TARGET = ("lp_stage_v1", "lp_stage", "127.0.0.1", "5432")
TOOL_SCRIPT = '#!/bin/sh\necho "tool ran: $(basename "$0") $*" >> "$TOOL_LOG"\nexit 99\n'


def verify_target():
    spec = importlib.util.spec_from_file_location("verify_target", KIT / "verify_target.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_tools(directory, script, *names):
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_text(script)
        (directory / name).chmod(0o755)


class StageScriptTests(SimpleTestCase):
    def run_stage(self, *args, mode=0o600, user=None, proof_exit=None, **changes):
        with tempfile.TemporaryDirectory() as home:
            backend = Path(home) / "lion-parts" / "backend"
            backend.mkdir(parents=True)
            env_file = backend / ".env"
            env_file.write_text("".join(f"{key}={value}\n" for key, value in {**GOOD, **changes}.items()))
            env_file.chmod(mode)
            env = {"PATH": os.environ["PATH"], "STAGE_USER": user or getpass.getuser(), "STAGE_HOME": home,
                   "STAGE_CHECKOUT": str(backend.parent), "STAGE_ENV_FILE": str(env_file)}
            if proof_exit is not None:
                # Stand-ins: the proof answers as told, and anything reaching a database tool is logged instead of connecting.
                add_tools(backend / "venv" / "bin", f'#!/bin/sh\necho "proof: $*"\nexit {proof_exit}\n', "python")
                tools = Path(home) / "tools"
                add_tools(tools, TOOL_SCRIPT, "psql", "pg_dump", "pg_restore", "curl")
                env |= {"PATH": f"{tools}:{env['PATH']}", "TOOL_LOG": str(Path(home) / "tools.log")}
            result = subprocess.run(["bash", str(KIT / "stage.sh"), *args], env=env, capture_output=True, text=True, timeout=20)
            result.tools_ran = Path(home, "tools.log").read_text() if Path(home, "tools.log").exists() else ""
            return result

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

    def test_any_other_database_role_is_refused(self):
        for role in ("lion_parts", "lion_parts_dev", "postgres", "lp_stage2", "LP_STAGE", ""):
            with self.subTest(role=role):
                self.assert_refused(self.run_stage("check", DB_USER=role), "DB_USER must be 'lp_stage'")

    def test_any_other_database_host_is_refused(self):
        for host in ("localhost", "10.0.0.5", "127.0.0.2", "/var/run/postgresql", ""):
            with self.subTest(host=host):
                self.assert_refused(self.run_stage("check", DB_HOST=host), "DB_HOST must be '127.0.0.1'")

    def test_any_other_database_port_is_refused(self):
        for port in ("5433", "6432", "05432", ""):
            with self.subTest(port=port):
                self.assert_refused(self.run_stage("check", DB_PORT=port), "DB_PORT must be '5432'")

    def test_every_command_on_the_database_stops_if_the_proof_fails(self):
        dump = str(KIT / "stage.sh")  # any file that exists: nothing must read it
        for args, changes in ((("check",), {}), (("health",), {}), (("backup",), {}), (("restore-verify", dump), {}),
                              (("restore", dump), {"CONFIRM": "lp_stage_v1"})):
            with self.subTest(command=args[0]):
                result = self.run_stage(*args, proof_exit=3, **changes)
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertIn("proof: ", result.stdout)
                self.assertIn(" ".join(TARGET), result.stdout)
                self.assertEqual(result.tools_ran, "")

    def test_a_redirecting_libpq_setting_is_cleared(self):
        text = (KIT / "stage.sh").read_text()
        self.assertIn("unset PGHOSTADDR PGSERVICE", text)
        self.assertLess(text.index('. "$ENV_FILE"'), text.index("unset PGHOSTADDR"))

    def test_another_user_and_a_readable_env_file_are_refused(self):
        self.assert_refused(self.run_stage("check", user="someone-else"), "run as someone-else")
        self.assertIn("mode 600", self.run_stage("check", mode=0o644).stderr)

    def test_replacing_the_database_needs_the_explicit_confirmation(self):
        self.assert_refused(self.run_stage("restore", "/nonexistent"), "set CONFIRM=lp_stage_v1")

    def test_an_unknown_command_does_nothing(self):
        self.assertEqual(self.run_stage("drop").returncode, 2)


class TargetProofTests(SimpleTestCase):
    @staticmethod
    def staging(**database):
        db = {"NAME": "lp_stage_v1", "USER": "lp_stage", "HOST": "127.0.0.1", "PORT": "5432", **database}
        return SimpleNamespace(DATABASES={"default": db}, **verify_target().SETTINGS)

    def test_the_right_settings_pass(self):
        self.assertEqual(verify_target().settings_problems(self.staging(), *TARGET), {})

    def test_the_effective_database_settings_must_match_exactly(self):
        for key, value in (("NAME", "lion_parts"), ("USER", "postgres"), ("HOST", "localhost"), ("HOST", "10.0.0.5"), ("PORT", "5433")):
            with self.subTest(key=key, value=value):
                self.assertEqual(verify_target().settings_problems(self.staging(**{key: value}), *TARGET), {key: value})

    def test_other_effective_settings_are_still_checked(self):
        staging = self.staging()
        staging.PARTS_PROVIDER = "amt"
        self.assertEqual(verify_target().settings_problems(staging, *TARGET), {"PARTS_PROVIDER": "amt"})

    def test_the_right_connection_passes(self):
        row = ("lp_stage_v1", "lp_stage", "lp_stage", "127.0.0.1", 5432, "off")
        self.assertEqual(verify_target().connection_problems(row, *TARGET), {})

    def test_the_live_connection_must_match_exactly(self):
        good = ["lp_stage_v1", "lp_stage", "lp_stage", "127.0.0.1", 5432, "off"]
        for index, name, value in ((0, "database", "lion_parts"), (1, "current_user", "postgres"), (2, "session_user", "postgres"),
                                   (3, "host", "10.0.0.5"), (3, "host", None), (5, "superuser", "on")):
            with self.subTest(name=name, value=value):
                row = list(good)
                row[index] = value
                self.assertEqual(verify_target().connection_problems(row, *TARGET), {name: value})
        row = list(good)
        row[4] = 5433
        self.assertEqual(verify_target().connection_problems(row, *TARGET), {"port": "5433"})


@unittest.skipUnless(connection.vendor == "postgresql", "asks a real PostgreSQL server")
class LiveConnectionProofTests(TestCase):
    def test_the_identity_query_reports_this_connection(self):
        row = verify_target().read_identity(connection)
        db = settings.DATABASES["default"]
        self.assertEqual(row[0], db["NAME"])
        self.assertEqual(row[1], db["USER"])
        self.assertEqual(row[2], db["USER"])
        self.assertEqual(str(row[4]), str(db["PORT"]))
        self.assertIn(row[5], ("on", "off"))
        self.assertIn("database", verify_target().connection_problems(row, *TARGET))


class ProvisionFailureTests(SimpleTestCase):
    def test_a_failure_part_way_removes_nothing_and_points_to_the_runbook(self):
        if Path("/home/lionparts-stage").exists() or Path("/etc/systemd/system/lionparts-stage.service").exists():
            self.skipTest("a real staging exists on this host")
        with tempfile.TemporaryDirectory() as temp:
            tools, log = Path(temp) / "tools", Path(temp) / "log"
            # Stand-ins for every command that could change the host. Root is pretended, and the clone fails.
            script = f"""#!/bin/sh
echo "$(basename "$0") $*" >> "{log}"
case "$(basename "$0") $*" in
    "id -u") echo 0 ;;
    id*) exit 1 ;;
    *"git clone"*) exit 1 ;;
esac
exit 0
"""
            add_tools(tools, script, "id", "ss", "sudo", "useradd", "passwd", "chmod", "install", "systemctl", "python3",
                      "rm", "userdel", "deluser", "groupdel", "dropdb", "dropuser")
            result = subprocess.run(["bash", str(KIT / "provision.sh"), "--apply"], capture_output=True, text=True, timeout=20,
                                    env={"PATH": f"{tools}:{os.environ['PATH']}"})
            ran = log.read_text()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAILED part-way. Nothing was removed.", result.stderr)
        self.assertIn("docs/staging-runbook.md", result.stderr)
        self.assertIn("creating: Linux user lionparts-stage", result.stdout)
        self.assertIn("useradd ", ran)
        self.assertNotIn("CREATE ROLE", ran + result.stdout)
        for removal in ("rm ", "userdel", "deluser", "groupdel", "dropdb", "dropuser"):
            self.assertNotIn(removal, ran)

    def test_the_script_holds_no_way_to_remove_a_resource(self):
        text = (KIT / "provision.sh").read_text()
        for removal in ("DROP ", "userdel", "deluser", "rm -", "dropdb", "dropuser", "systemctl disable"):
            self.assertNotIn(removal, text)


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

    def test_nginx_serves_only_django_and_no_private_path(self):
        text = (KIT / "nginx-stage.conf.template").read_text()
        self.assertEqual(re.findall(r"\balias (\S+);", text), ["${STAGE_HOME}/lion-parts/backend/staticfiles/", "${STAGE_HOME}/media/"])
        self.assertNotIn("autoindex", text)
        self.assertIn("location / { return 404; }", text)
        self.assertIn("LPK-223", text)
        self.assertNotRegex(text, r"(?m)^\s*root\s")

    def test_nginx_logs_are_outside_the_staging_home(self):
        text = (KIT / "nginx-stage.conf.template").read_text()
        for line in text.splitlines():
            if line.strip().startswith(("access_log", "error_log")):
                self.assertIn("/var/log/nginx/lionparts-stage-", line)

    def test_provision_gives_nginx_a_path_in_but_keeps_backups_private(self):
        text = (KIT / "provision.sh").read_text()
        self.assertIn('chmod 711 "$HOME_DIR"\n', text)
        self.assertIn('chmod 711 "$HOME_DIR/lion-parts" "$HOME_DIR/lion-parts/backend"', text)
        self.assertIn('install -d -m 755 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/media"', text)
        self.assertIn('install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/backups"', text)
        self.assertIn('install -m 600 -o "$USER_NAME" -g "$USER_NAME" "$HERE/env.staging.example" "$env_file"', text)
        self.assertNotIn("usermod", text)
        self.assertNotIn("www-data", text)

    def test_the_runbook_keeps_superuser_passwords_off_the_command_line(self):
        text = (KIT.parent.parent / "docs" / "staging-runbook.md").read_text()
        self.assertIn("**exactly two**", text)
        self.assertNotIn("UAT_STAFF_PASSWORD=<", text)
        self.assertNotIn("createsuperuser --", text)

    def test_the_example_env_file_passes_the_scripts_own_rules(self):
        lines = (KIT / "env.staging.example").read_text().splitlines()
        values = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
        for key, value in GOOD.items():
            if key not in ("DB_PASSWORD", "SECRET_KEY"):
                self.assertEqual(values[key], value, key)
