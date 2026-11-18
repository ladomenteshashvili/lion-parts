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

from config.tests import run_python

KIT = Path(settings.BASE_DIR).parent / "deploy" / "staging"
GOOD = {"DEPLOY_ENV": "staging", "DEBUG": "False", "PARTS_PROVIDER": "demo", "PAYMENT_PROVIDER": "fake", "SMS_DEMO_MODE": "True",
        "SENDER_GE_ENABLED": "False", "ENABLE_DEMO_ENDPOINTS": "True", "DB_NAME": "lp_stage_v1", "DB_USER": "lp_stage",
        "DB_HOST": "127.0.0.1", "DB_PORT": "5432", "DB_PASSWORD": "x", "EMAIL_HOST": "127.0.0.1", "SECRET_KEY": "x"}
# The approved real profiles beside the demo one above: real Sender.ge, then real Sender.ge and real AMT. Payments stay fake.
REAL_SMS = {**GOOD, "SENDER_GE_ENABLED": "True", "SENDER_GE_API_KEY": "k", "SMS_DEMO_MODE": "False", "ENABLE_DEMO_ENDPOINTS": "False"}
REAL_AMT = {**REAL_SMS, "PARTS_PROVIDER": "amt", "AMT_API_LOGIN": "l", "AMT_API_PASSWORD": "p"}
PROFILES = {"demo": GOOD, "real sms": REAL_SMS, "real amt": REAL_AMT}
# LPK-235: the Brevo relay instead of Mailpit, beside any provider profile.
BREVO_MAIL = {"EMAIL_HOST": "smtp-relay.brevo.com", "EMAIL_PORT": "587", "EMAIL_USE_TLS": "True",
              "EMAIL_HOST_USER": "login@example.invalid", "EMAIL_HOST_PASSWORD": "brevo-key-should-not-appear"}
SMTP = "django.core.mail.backends.smtp.EmailBackend"
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
    def run_stage(self, *args, mode=0o600, user=None, proof_exit=None, profile=GOOD, **changes):
        with tempfile.TemporaryDirectory() as home:
            backend = Path(home) / "lion-parts" / "backend"
            backend.mkdir(parents=True)
            env_file = backend / ".env"
            env_file.write_text("".join(f"{key}={value}\n" for key, value in {**profile, **changes}.items()))
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
        for name, profile in PROFILES.items():
            for key, value in (("DEPLOY_ENV", "production"), ("DEBUG", "True"), ("PAYMENT_PROVIDER", "flitt"),
                               ("PAYMENT_PROVIDER", ""), ("EMAIL_HOST", "smtp.example.com")):
                with self.subTest(profile=name, key=key, value=value):
                    self.assert_refused(self.run_stage("backup", profile=profile, **{key: value}), f"{key} must be")

    def test_only_the_approved_provider_profiles_pass(self):
        for parts, sms in (("amt", "False"), ("AMT", "True"), ("amt", ""), ("", "True"), ("demo", "true"), ("other", "True")):
            with self.subTest(parts=parts, sms=sms):
                self.assert_refused(self.run_stage("check", profile=REAL_AMT, PARTS_PROVIDER=parts, SENDER_GE_ENABLED=sms),
                                    "PARTS_PROVIDER/SENDER_GE_ENABLED must be demo/False, demo/True or amt/True")
        for name, profile in PROFILES.items():
            with self.subTest(profile=name):
                result = self.run_stage("check", profile=profile, proof_exit=0)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("target verified", result.stdout)
                self.assertEqual(result.tools_ran, "")

    def test_demo_tools_only_when_nothing_real_is_called(self):
        self.assert_refused(self.run_stage("check", SMS_DEMO_MODE="False"), "SMS_DEMO_MODE must be 'True'")
        for name, profile in (("real sms", REAL_SMS), ("real amt", REAL_AMT)):
            for key in ("SMS_DEMO_MODE", "ENABLE_DEMO_ENDPOINTS"):
                with self.subTest(profile=name, key=key):
                    self.assert_refused(self.run_stage("check", profile=profile, **{key: "True"}), f"{key} must be 'False'")

    def test_real_credentials_are_refused(self):
        for name, profile in PROFILES.items():
            for key in ("FLITT_MERCHANT_ID", "FLITT_SECRET_KEY", "EMAIL_HOST_PASSWORD"):
                with self.subTest(profile=name, key=key):
                    self.assert_refused(self.run_stage("check", profile=profile, **{key: "something"}), f"{key} must be empty")
        for key in ("AMT_API_LOGIN", "AMT_API_PASSWORD", "SENDER_GE_API_KEY"):
            with self.subTest(key=key):
                self.assert_refused(self.run_stage("check", **{key: "something"}), f"{key} must be empty")
        for key in ("AMT_API_LOGIN", "AMT_API_PASSWORD"):
            with self.subTest(key=key):
                self.assert_refused(self.run_stage("check", profile=REAL_SMS, **{key: "something"}), f"{key} must be empty")

    def test_a_real_provider_needs_its_credentials(self):
        for profile, key in ((REAL_AMT, "AMT_API_LOGIN"), (REAL_AMT, "AMT_API_PASSWORD"), (REAL_AMT, "SENDER_GE_API_KEY"),
                             (REAL_SMS, "SENDER_GE_API_KEY")):
            with self.subTest(key=key):
                self.assert_refused(self.run_stage("check", profile=profile, **{key: ""}), f"{key} must be set for this provider")

    def test_a_refusal_never_prints_a_credential(self):
        result = self.run_stage("check", profile=REAL_AMT, AMT_API_PASSWORD="pw-should-not-appear", DB_NAME="lion_parts")
        self.assert_refused(result, "DB_NAME")
        self.assertNotIn("pw-should-not-appear", result.stdout + result.stderr)

    def test_mailpit_and_brevo_pass_beside_every_provider_profile(self):
        for name, profile in PROFILES.items():
            for mail, changes in (("mailpit", {}), ("brevo", BREVO_MAIL)):
                with self.subTest(profile=name, mail=mail):
                    result = self.run_stage("check", profile=profile, proof_exit=0, **changes)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("target verified", result.stdout)

    def test_backup_health_and_restore_check_still_run_with_either_mail_profile(self):
        # Past every refusal to the first database tool (a stand-in that logs and fails). The nightly backup is stage.sh backup.
        dump = str(KIT / "stage.sh")
        for mail, changes in (("mailpit", {}), ("brevo", BREVO_MAIL)):
            for args, tool in ((("backup",), "pg_dump"), (("health",), "curl"), (("restore-verify", dump), "pg_restore")):
                with self.subTest(mail=mail, command=args[0]):
                    result = self.run_stage(*args, profile=REAL_AMT, proof_exit=0, ALLOWED_HOSTS="test2.lionparts.ge", **changes)
                    self.assertIn(f"tool ran: {tool}", result.tools_ran, result.stderr)

    def test_any_other_or_insecure_mail_server_is_refused(self):
        for host in ("smtp.example.com", "smtp-relay.brevo.com.example.com", "SMTP-RELAY.BREVO.COM", "smtp-relay.sendinblue.com",
                     "localhost", "127.0.0.2", "0.0.0.0", ""):
            with self.subTest(host=host):
                self.assert_refused(self.run_stage("check", **{**BREVO_MAIL, "EMAIL_HOST": host}),
                                    "EMAIL_HOST must be 127.0.0.1 (Mailpit) or smtp-relay.brevo.com (Brevo)")
        for key, value, reason in (("EMAIL_PORT", "25", "EMAIL_PORT must be '587'"), ("EMAIL_PORT", "465", "EMAIL_PORT must be '587'"),
                                   ("EMAIL_PORT", "2525", "EMAIL_PORT must be '587'"), ("EMAIL_PORT", "", "EMAIL_PORT must be '587'"),
                                   ("EMAIL_USE_TLS", "False", "EMAIL_USE_TLS must be 'True'"),
                                   ("EMAIL_USE_TLS", "", "EMAIL_USE_TLS must be 'True'"),
                                   ("EMAIL_HOST_USER", "", "EMAIL_HOST_USER must be set"),
                                   ("EMAIL_HOST_PASSWORD", "", "EMAIL_HOST_PASSWORD must be set")):
            with self.subTest(key=key, value=value):
                result = self.run_stage("backup", profile=REAL_AMT, **{**BREVO_MAIL, key: value})
                self.assert_refused(result, reason)
                self.assertNotIn("brevo-key-should-not-appear", result.stdout + result.stderr)

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
    DEMO = {"PARTS_PROVIDER": "demo", "SENDER_GE_ENABLED": False, "SMS_DEMO_MODE": True, "ENABLE_DEMO_ENDPOINTS": True,
            "AMT_API_LOGIN": "", "AMT_API_PASSWORD": "", "SENDER_GE_API_KEY": "", "AMT_TIMEOUT_SECONDS": 15,
            "AMT_LOOKUP_BUDGET_SECONDS": 30, "DEFAULT_FROM_EMAIL": "Lion Parts Staging <no-reply@example.invalid>",
            "MAILERS": {"default": {"BACKEND": SMTP, "OPTIONS": {"host": "127.0.0.1", "port": 1025, "username": "", "password": "",
                                                                 "use_tls": False, "timeout": 10}}}}
    REAL_AMT = {**DEMO, "PARTS_PROVIDER": "amt", "SENDER_GE_ENABLED": True, "SMS_DEMO_MODE": False, "ENABLE_DEMO_ENDPOINTS": False,
                "AMT_API_LOGIN": "secret-login", "AMT_API_PASSWORD": "secret-pass", "SENDER_GE_API_KEY": "secret-key"}

    @classmethod
    def staging(cls, profile=None, **changes):
        database = {key: changes.pop(key) for key in ("NAME", "USER", "HOST", "PORT") if key in changes}
        db = {"NAME": "lp_stage_v1", "USER": "lp_stage", "HOST": "127.0.0.1", "PORT": "5432", **database}
        return SimpleNamespace(DATABASES={"default": db}, **{**verify_target().SETTINGS, **(profile or cls.DEMO), **changes})

    def problems(self, profile=None, **changes):
        return verify_target().settings_problems(self.staging(profile, **changes), *TARGET)

    @staticmethod
    def brevo(backend=SMTP, from_email="Lion Parts Staging <verify@lionparts.ge>", **options):
        brevo = {"host": "smtp-relay.brevo.com", "port": 587, "username": "secret-login", "password": "secret-smtp-key",
                 "use_tls": True, "timeout": 10}
        return {"MAILERS": {"default": {"BACKEND": backend, "OPTIONS": brevo | options}}, "DEFAULT_FROM_EMAIL": from_email}

    def real_settings_problems(self, env):
        # The defaults settings.py gives an approved .env (endpoints, TLS, timeouts, mail backend) must satisfy the proof.
        env = {**env, "SECRET_KEY": "x" * 60, "ALLOWED_HOSTS": "test2.lionparts.ge", "AMT_ENDPOINT": None, "AMT_VERIFY_SSL": None,
               "AMT_TIMEOUT_SECONDS": None, "AMT_LOOKUP_BUDGET_SECONDS": None, "SENDER_GE_SEND_URL": None, "EMAIL_BACKEND": None,
               "EMAIL_TIMEOUT": None}
        code = (f"import importlib.util, config.settings as s; spec = importlib.util.spec_from_file_location('v', {str(KIT / 'verify_target.py')!r}); "
                f"m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(m.settings_problems(s, *{TARGET!r}))")
        result = run_python(code, env)
        return result.stdout.strip() or result.stderr

    def test_the_approved_profiles_pass(self):
        real_sms = {**self.REAL_AMT, "PARTS_PROVIDER": "demo", "AMT_API_LOGIN": "", "AMT_API_PASSWORD": ""}
        for profile in (self.DEMO, real_sms, self.REAL_AMT, {**self.REAL_AMT, "AMT_TIMEOUT_SECONDS": 5, "AMT_LOOKUP_BUDGET_SECONDS": 10}):
            with self.subTest(profile=profile["PARTS_PROVIDER"]):
                self.assertEqual(self.problems(profile), {})

    def test_the_effective_database_settings_must_match_exactly(self):
        for key, value in (("NAME", "lion_parts"), ("USER", "postgres"), ("HOST", "localhost"), ("HOST", "10.0.0.5"), ("PORT", "5433")):
            with self.subTest(key=key, value=value):
                self.assertEqual(self.problems(**{key: value}), {key: value})
                self.assertEqual(self.problems(self.REAL_AMT, **{key: value}), {key: value})

    def test_other_effective_settings_are_still_checked(self):
        for key, value in (("DEPLOY_ENV", "production"), ("DEBUG", True), ("PAYMENT_PROVIDER", "flitt"), ("AMT_VERIFY_SSL", False),
                           ("AMT_ENDPOINT", "http://127.0.0.1:9000/"), ("SENDER_GE_SEND_URL", "https://sender.example/send")):
            with self.subTest(key=key):
                self.assertEqual(self.problems(self.REAL_AMT, **{key: value}), {key: value})

    def test_an_unapproved_profile_is_refused(self):
        for parts, sms in (("amt", False), ("AMT", True), ("other", True), ("demo", "True")):
            with self.subTest(parts=parts, sms=sms):
                self.assertIn("PARTS_PROVIDER/SENDER_GE_ENABLED", self.problems(self.REAL_AMT, PARTS_PROVIDER=parts, SENDER_GE_ENABLED=sms))

    def test_demo_tools_are_refused_beside_a_real_provider(self):
        for key in ("SMS_DEMO_MODE", "ENABLE_DEMO_ENDPOINTS"):
            with self.subTest(key=key):
                self.assertEqual(self.problems(self.REAL_AMT, **{key: True}), {key: True})

    def test_credentials_only_beside_their_provider_and_never_by_value(self):
        self.assertEqual(self.problems(self.REAL_AMT, AMT_API_PASSWORD=""), {"AMT_API_PASSWORD set": False})
        self.assertEqual(self.problems(self.REAL_AMT, SENDER_GE_API_KEY=""), {"SENDER_GE_API_KEY set": False})
        self.assertEqual(self.problems(AMT_API_LOGIN="secret-login"), {"AMT_API_LOGIN set": True})
        self.assertEqual(self.problems(SENDER_GE_API_KEY="secret-key"), {"SENDER_GE_API_KEY set": True})
        wrong = self.problems(self.REAL_AMT, PARTS_PROVIDER="demo", DEBUG=True)
        self.assertNotRegex(repr(wrong), "secret-")

    def test_the_amt_lookup_stays_within_the_timeout_and_budget(self):
        for timeout, budget in ((16, 30), (15, 31), (0, 30), (20, 10), (-1, 5)):
            with self.subTest(timeout=timeout, budget=budget):
                self.assertEqual(self.problems(self.REAL_AMT, AMT_TIMEOUT_SECONDS=timeout, AMT_LOOKUP_BUDGET_SECONDS=budget),
                                 {"AMT_TIMEOUT_SECONDS/AMT_LOOKUP_BUDGET_SECONDS": (timeout, budget)})

    def test_the_real_settings_module_passes_with_the_real_amt_profile(self):
        self.assertEqual(self.real_settings_problems(REAL_AMT), "{}")

    def test_the_real_settings_module_passes_with_brevo_and_refuses_plain_smtp(self):
        brevo = {**REAL_AMT, **BREVO_MAIL, "DEFAULT_FROM_EMAIL": "Lion Parts Staging <verify@lionparts.ge>"}
        self.assertEqual(self.real_settings_problems(brevo), "{}")
        self.assertEqual(self.real_settings_problems({**brevo, "EMAIL_USE_TLS": "False"}), "{'EMAIL_USE_TLS': False}")

    def test_mailpit_and_brevo_pass(self):
        for profile in (self.DEMO, self.REAL_AMT):
            with self.subTest(profile=profile["PARTS_PROVIDER"]):
                self.assertEqual(self.problems(profile), {})
                for sender in ("Lion Parts Staging <verify@lionparts.ge>", "backup@lionparts.ge", "Lion Parts <verify@LionParts.GE>"):
                    self.assertEqual(self.problems({**profile, **self.brevo(from_email=sender)}), {}, sender)
                self.assertEqual(self.problems({**profile, **self.brevo(timeout=30)}), {})

    def test_an_insecure_or_unapproved_mailer_is_refused(self):
        console = "django.core.mail.backends.console.EmailBackend"
        mailpit = self.DEMO["MAILERS"]["default"]
        cases = (
            (self.brevo(port=25), {"EMAIL_PORT": 25}),
            (self.brevo(port=465, use_tls=False, use_ssl=True), {"EMAIL_PORT": 465, "EMAIL_USE_TLS": False, "EMAIL_USE_SSL": True}),
            (self.brevo(use_tls=False), {"EMAIL_USE_TLS": False}),
            (self.brevo(username=""), {"EMAIL_HOST_USER set": False}),
            (self.brevo(password=""), {"EMAIL_HOST_PASSWORD set": False}),
            (self.brevo(timeout=31), {"EMAIL_TIMEOUT": 31}),
            (self.brevo(timeout=None), {"EMAIL_TIMEOUT": 0}),
            (self.brevo(host="smtp.example.com"), {"EMAIL_HOST": "smtp.example.com", "EMAIL_HOST_PASSWORD set": True}),
            (self.brevo(backend=console), {"EMAIL_BACKEND": console}),
            (self.brevo(from_email="Lion Parts <no-reply@example.com>"), {"sender domain": "example.com"}),
            (self.brevo(from_email="verify@lionparts.ge.example.com"), {"sender domain": "lionparts.ge.example.com"}),
            (self.brevo(from_email="verify@mail.lionparts.ge"), {"sender domain": "mail.lionparts.ge"}),
            ({"MAILERS": {"default": {**mailpit, "OPTIONS": {**mailpit["OPTIONS"], "password": "secret-smtp-key"}}}},
             {"EMAIL_HOST_PASSWORD set": True}),
            ({"MAILERS": {"default": {"BACKEND": console, "OPTIONS": {}}}}, {"EMAIL_BACKEND": console, "EMAIL_HOST": None}),
        )
        for mail, wrong in cases:
            with self.subTest(wrong=wrong):
                problems = self.problems({**self.REAL_AMT, **mail})
                self.assertEqual(problems, wrong)
                self.assertNotRegex(repr(problems), "secret-")

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
