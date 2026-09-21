"""Status line installer tests, for both the Python and the PowerShell installer.
The rule they share: change the one statusLine key, never anything else, never someone else's."""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PY_INSTALLER = os.path.join(REPO, "python", "install_statusline.py")
PS_INSTALLER = os.path.join(REPO, "windows", "Install-StatusLine.ps1")

# Deliberately nested four levels deep: PowerShell's JSON writer flattens below two unless told not to.
EXISTING = {
    "permissions": {"defaultMode": "ask", "allow": ["Bash(git status)"]},
    "model": "opus",
    "enabledPlugins": {"example@market": True},
    "deep": {"a": {"b": {"c": {"d": [1, 2, {"e": "kept"}]}}}},
    "emptyList": [],
}
FOREIGN = {"type": "command", "command": "~/.claude/my-own-statusline.sh", "padding": 2}


class InstallerContract:
    """Shared tests. Subclasses say how to run their installer."""

    marker = ""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cuw-install-")
        self.settings = os.path.join(self.tmp, "claude", "settings.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, data):
        os.makedirs(os.path.dirname(self.settings), exist_ok=True)
        with open(self.settings, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

    def get(self):
        with open(self.settings, encoding="utf-8-sig") as handle:
            return json.load(handle)

    def test_check_changes_nothing(self):
        code, out = self.run_installer("check")
        self.assertEqual(code, 0, out)
        self.assertIn("none configured", out)
        self.assertFalse(os.path.exists(self.settings))

    def test_install_adds_only_the_status_line_and_backs_up(self):
        self.put(EXISTING)
        code, out = self.run_installer("install")
        self.assertEqual(code, 0, out)
        after = self.get()
        block = after.pop("statusLine")
        self.assertEqual(after, EXISTING, "every other setting must survive untouched")
        self.assertEqual((block["type"], block["refreshInterval"]), ("command", 60))
        self.assertIn(self.marker, block["command"])
        self.assertNotIn("\\", block["command"], "Git Bash eats backslashes, the command must use forward slashes")
        self.assertEqual(len(glob.glob(self.settings + ".bak-*")), 1)

        code, out = self.run_installer("install")
        self.assertEqual(code, 0, out)
        self.assertIn("Already installed", out)
        code, out = self.run_installer("check")
        self.assertIn("up to date", out)

    def test_install_creates_settings_when_there_are_none(self):
        code, out = self.run_installer("install")
        self.assertEqual(code, 0, out)
        self.assertEqual(list(self.get()), ["statusLine"])

    def test_someone_elses_status_line_is_never_replaced_without_force(self):
        self.put(dict(EXISTING, statusLine=FOREIGN))
        with open(self.settings, "rb") as handle:
            before = handle.read()
        for action in ("install", "remove"):
            code, out = self.run_installer(action)
            self.assertEqual(code, 2, out)
            with open(self.settings, "rb") as handle:
                self.assertEqual(handle.read(), before, "%s must not touch the file" % action)

        code, out = self.run_installer("install", force=True)
        self.assertEqual(code, 0, out)
        self.assertIn(self.marker, self.get()["statusLine"]["command"])

    def test_remove_takes_out_only_ours(self):
        self.put(EXISTING)
        self.run_installer("install")
        code, out = self.run_installer("remove")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.get(), EXISTING)


class PythonInstaller(InstallerContract, unittest.TestCase):
    marker = "ratelimit_feed.py"

    def run_installer(self, action, force=False):
        args = [sys.executable, PY_INSTALLER, action, "--settings", self.settings] + (["--force"] if force else [])
        done = subprocess.run(args, capture_output=True, text=True, timeout=60)
        return done.returncode, done.stdout + done.stderr


@unittest.skipUnless(shutil.which("pwsh") and os.path.isfile(PS_INSTALLER),
                     "needs PowerShell 7 and the windows/ folder (absent from the macOS and Linux zip)")
class PowerShellInstaller(InstallerContract, unittest.TestCase):
    marker = "Write-RateLimitFeed.ps1"

    def run_installer(self, action, force=False):
        args = ["pwsh", "-NoProfile", "-File", PS_INSTALLER, "-Action", action.capitalize(), "-SettingsPath", self.settings]
        done = subprocess.run(args + (["-Force"] if force else []), capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout + done.stderr

    def test_command_carries_the_execution_policy_flag(self):
        self.run_installer("install")
        self.assertIn("-ExecutionPolicy Bypass", self.get()["statusLine"]["command"])


if __name__ == "__main__":
    unittest.main()
