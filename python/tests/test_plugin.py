"""Claude Code plugin tests: the manifests, the skills, and the entry point each skill calls.

The entry point is exercised for real on whatever system runs the tests (widget.ps1 on Windows,
widget.sh elsewhere), against temp folders, so CI covers all three."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PLUGIN_JSON = os.path.join(REPO, ".claude-plugin", "plugin.json")
MARKETPLACE_JSON = os.path.join(REPO, ".claude-plugin", "marketplace.json")
SKILLS = os.path.join(REPO, "skills")
WINDOWS = os.name == "nt"

# The release zips carry one edition and no plugin files, so there is nothing to test there.
HAS_PLUGIN = os.path.isfile(PLUGIN_JSON) and os.path.isdir(os.path.join(REPO, "plugin"))


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@unittest.skipUnless(HAS_PLUGIN, "the plugin files are not part of the release zips")
class Manifests(unittest.TestCase):
    def test_plugin_manifest(self):
        plugin = load(PLUGIN_JSON)
        self.assertRegex(plugin["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$", "plugin names are kebab-case and immutable once published")
        self.assertRegex(plugin["version"], r"^\d+\.\d+\.\d+$",
                         "users are pinned to this string: bump it on every release or they never update")
        self.assertEqual(plugin["license"], "MIT")
        for field in ("description", "homepage", "repository"):
            self.assertTrue(plugin[field])

    def test_names_avoid_anthropic_marks(self):
        # Anthropic's terms: its names may describe what a product works with ("for Claude Code"),
        # but may not be part of the product's own name.
        plugin, market = load(PLUGIN_JSON), load(MARKETPLACE_JSON)
        for name in (plugin["name"], market["name"], market["plugins"][0]["name"], market["plugins"][0].get("displayName", "")):
            self.assertNotRegex(name.lower(), r"claude|anthropic", name)

    def test_marketplace_lists_this_repo_as_its_one_plugin(self):
        plugin, market = load(PLUGIN_JSON), load(MARKETPLACE_JSON)
        self.assertEqual(len(market["plugins"]), 1)
        entry = market["plugins"][0]
        self.assertEqual(entry["name"], plugin["name"])
        self.assertEqual(entry["source"], "./", "the repository root is the plugin, so windows/ and python/ ship with it")
        self.assertNotIn("version", entry, "plugin.json is the one place the version lives")
        self.assertTrue(market["owner"]["name"])

    def test_only_manifests_live_in_the_claude_plugin_folder(self):
        self.assertEqual(sorted(os.listdir(os.path.dirname(PLUGIN_JSON))), ["marketplace.json", "plugin.json"])
        self.assertFalse(os.path.exists(os.path.join(REPO, "CLAUDE.md")),
                         "a root CLAUDE.md is not loaded for plugin users and fails strict validation; it lives in .claude/")


@unittest.skipUnless(HAS_PLUGIN, "the plugin files are not part of the release zips")
class Skills(unittest.TestCase):
    EXPECTED = {"start", "stop", "usage", "status", "setup", "uninstall"}
    USER_ONLY = {"setup", "uninstall"}          # they change the user's settings or delete things

    def skills(self):
        found = {}
        for path in glob.glob(os.path.join(SKILLS, "*", "SKILL.md")):
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            front = re.match(r"^---\n(.*?)\n---\n", text, re.S)
            self.assertIsNotNone(front, path)
            found[os.path.basename(os.path.dirname(path))] = (front.group(1), text[front.end():])
        return found

    def test_every_skill_is_well_formed_and_calls_the_entry_point(self):
        skills = self.skills()
        self.assertEqual(set(skills), self.EXPECTED)
        for folder, (front, body) in skills.items():
            self.assertRegex(front, r"(?m)^name: %s$" % folder, "the name must match its folder")
            description = re.search(r"(?m)^description: (.+)$", front).group(1)
            self.assertGreater(len(description), 40, folder)
            self.assertLess(len(description), 1536, folder)
            for script in ("widget.sh", "widget.ps1"):
                command = '"${CLAUDE_PLUGIN_ROOT}/plugin/%s" %s' % (script, folder)
                self.assertIn(command, body, "%s must run its own verb" % folder)
                self.assertIn('"${CLAUDE_PLUGIN_ROOT}/plugin/%s" *)' % script, front, "allowed-tools must match the command")
            self.assertTrue(os.path.isfile(os.path.join(REPO, "plugin", "widget.sh")))

    def test_skills_that_change_things_are_started_by_the_user_only(self):
        for folder, (front, _body) in self.skills().items():
            self.assertEqual("disable-model-invocation: true" in front, folder in self.USER_ONLY, folder)


@unittest.skipUnless(HAS_PLUGIN, "the plugin files are not part of the release zips")
class EntryPoint(unittest.TestCase):
    """Runs the real script for this system. CLAUDE_USAGE_WIDGET_APP_DIR and CLAUDE_CONFIG_DIR point
    at temp folders, so nothing on the machine running the tests is touched."""

    def setUp(self):
        # realpath: Windows may hand out an 8.3 short path (USERNA~1), macOS a /var symlink. The
        # scripts write the real path, so compare like with like.
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cuw-plugin-"))
        self.app = os.path.join(self.tmp, "app dir")            # a space on purpose
        self.config = os.path.join(self.tmp, "claude")
        os.makedirs(self.config)
        self.settings = os.path.join(self.config, "settings.json")
        self.env = dict(os.environ, CLAUDE_USAGE_WIDGET_APP_DIR=self.app, CLAUDE_CONFIG_DIR=self.config,
                        CLAUDE_USAGE_WIDGET_STATE_DIR=os.path.join(self.tmp, "state"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_verb(self, verb, force=False):
        if WINDOWS:
            command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                       os.path.join(REPO, "plugin", "widget.ps1"), verb] + (["-Force"] if force else [])
        else:
            command = ["sh", os.path.join(REPO, "plugin", "widget.sh"), verb] + (["--force"] if force else [])
        done = subprocess.run(command, capture_output=True, text=True, env=self.env, timeout=180)
        return done.returncode, done.stdout + done.stderr

    def settings_now(self):
        with open(self.settings, encoding="utf-8-sig") as handle:
            return json.load(handle)

    def test_sync_copies_the_app_to_the_stable_folder(self):
        code, out = self.run_verb("sync")
        self.assertEqual(code, 0, out)
        edition = "windows" if WINDOWS else "python"
        copied = os.listdir(os.path.join(self.app, edition))
        self.assertIn("ClaudeUsageWidget.ps1" if WINDOWS else "claude_usage_widget.py", copied)
        self.assertTrue(os.path.isfile(os.path.join(self.app, "pricing.json")), "the engine looks one folder up for prices")
        with open(os.path.join(self.app, "VERSION")) as handle:
            self.assertEqual(handle.read().strip(), load(PLUGIN_JSON)["version"])

    def test_status_changes_nothing(self):
        code, out = self.run_verb("status")
        self.assertEqual(code, 0, out)
        self.assertIn("not running", out)
        self.assertFalse(os.path.exists(self.app))
        self.assertFalse(os.path.exists(self.settings))

    def test_usage_prints_numbers(self):
        code, out = self.run_verb("usage")
        self.assertEqual(code, 0, out)
        self.assertIn("Unique responses", out)

    def test_setup_points_the_status_line_at_the_stable_copy_never_at_the_plugin_folder(self):
        existing = {"model": "opus", "deep": {"a": {"b": {"c": [1, 2]}}}}
        with open(self.settings, "w", encoding="utf-8") as handle:
            json.dump(existing, handle)
        code, out = self.run_verb("setup")
        self.assertEqual(code, 0, out)
        after = self.settings_now()
        command = after.pop("statusLine")["command"]
        self.assertEqual(after, existing, "every other setting must survive")
        self.assertIn(self.app.replace("\\", "/"), command,
                      "the plugin folder moves on every update, so the status line must use the stable copy")
        self.assertNotIn(REPO.replace("\\", "/") + "/plugin", command)
        self.assertNotIn("\\", command, "Git Bash eats backslashes")

        code, out = self.run_verb("uninstall")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.settings_now(), existing, "uninstall removes our status line and nothing else")
        self.assertFalse(os.path.exists(self.app))

    def test_a_running_widget_is_found_and_stop_kills_only_the_widget(self):
        # Two bugs lived here. Exactly one running widget was reported as "not running" (in
        # PowerShell a single match is one object, and a process object has no Count). And the
        # match was loose enough that "stop" would have killed any shell that mentioned the file.
        import time
        edition, name = ("windows", "ClaudeUsageWidget.ps1") if WINDOWS else ("python", "claude_usage_widget.py")
        os.makedirs(os.path.join(self.app, edition))
        stub = os.path.join(self.app, edition, name)
        with open(stub, "w") as handle:
            handle.write("Start-Sleep -Seconds 90\n" if WINDOWS else "import time\ntime.sleep(90)\n")
        if WINDOWS:
            widget = subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", stub])
            decoy = subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", "Start-Sleep -Seconds 90 # editing " + stub])
        else:
            widget = subprocess.Popen([sys.executable, stub])
            decoy = subprocess.Popen(["sh", "-c", "sleep 90; : editing '%s'" % stub])
        try:
            wanted = "running, pid %d" % widget.pid
            out = ""
            for _attempt in range(30):
                _code, out = self.run_verb("status")
                if wanted in out:
                    break
                time.sleep(0.5)
            self.assertIn(wanted, out, "one running widget must be reported, with the widget's pid and not the decoy's")

            code, out = self.run_verb("stop")
            self.assertEqual(code, 0, out)
            self.assertIn("stopped", out)
            widget.wait(timeout=20)
            self.assertIsNone(decoy.poll(), "a process that only mentions the script must survive stop")
            _code, out = self.run_verb("status")
            self.assertIn("not running", out)
        finally:
            for process in (widget, decoy):
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=20)

    def test_someone_elses_status_line_is_refused_with_exit_code_2(self):
        mine = {"statusLine": {"type": "command", "command": "~/.claude/my-own-statusline.sh"}}
        with open(self.settings, "w", encoding="utf-8") as handle:
            json.dump(mine, handle)
        code, out = self.run_verb("setup")
        self.assertEqual(code, 2, out)
        self.assertEqual(self.settings_now(), mine)

        code, out = self.run_verb("uninstall")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.settings_now(), mine, "uninstall must leave a status line that is not ours alone")

        code, out = self.run_verb("setup", force=True)
        self.assertEqual(code, 0, out)
        self.assertIn("ratelimit_feed.py" if not WINDOWS else "Write-RateLimitFeed.ps1", self.settings_now()["statusLine"]["command"])


if __name__ == "__main__":
    unittest.main()
