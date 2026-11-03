"""M6 review: the legacy scripts/deploy-staging.sh is disabled. It would check out v1 and run `migrate` against
the legacy staging database (release blocker B2). The guard must stop it before any command runs."""

import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

SCRIPT = Path(settings.BASE_DIR).parent / "scripts" / "deploy-staging.sh"
COMMANDS = ("git", "sudo", "python", "python3", "pip", "npm", "systemctl", "nginx", "rm", "cp", "mkdir", "chown",
            "cat", "date", "source")


class LegacyDeployScriptTests(SimpleTestCase):
    def run_with_stubs(self):
        """Run the script with every command it could use replaced by a stub that records the call."""
        with tempfile.TemporaryDirectory() as stubs:
            log = Path(stubs) / "calls.log"
            for name in COMMANDS:
                stub = Path(stubs) / name
                stub.write_text(f"#!/bin/sh\necho {name} \"$@\" >> {log}\n")
                stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
            bash = shutil.which("bash")
            env = {"PATH": stubs, "HOME": stubs}
            result = subprocess.run([bash, str(SCRIPT)], cwd=stubs, env=env, capture_output=True, text=True,
                                    timeout=30)
            calls = log.read_text() if log.exists() else ""
        return result, calls

    def test_the_script_exits_nonzero_before_running_anything(self):
        result, calls = self.run_with_stubs()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, "", "no git pull, migrate, rm, build or restart may run")
        self.assertIn("legacy", result.stderr)
        self.assertIn("docs/operations.md", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_the_guard_comes_before_every_command(self):
        """Up to the first `exit 1` there are only comments and echo lines (no set, cd, git, ...)."""
        lines = SCRIPT.read_text().splitlines()
        guard_end = next(i for i, line in enumerate(lines) if line.strip() == "exit 1")
        for line in lines[1:guard_end]:
            self.assertTrue(not line.strip() or line.lstrip().startswith(("#", "echo ")), line)
        self.assertTrue(lines[0].startswith("#!"))
        self.assertTrue(re.search(r"migrate", "\n".join(lines[guard_end:])))  # the danger is below the guard

    def test_the_script_is_still_executable_and_points_to_the_runbook(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        self.assertIn("docs/operations.md", SCRIPT.read_text())
