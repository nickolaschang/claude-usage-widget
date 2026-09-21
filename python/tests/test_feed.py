"""Status line feed tests. Runs the script the way Claude Code does: JSON on stdin, text on stdout."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import claude_usage as cu  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(HERE), "ratelimit_feed.py")


class Feed(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="cuw-feed-")
        self.feed = os.path.join(self.state, "ratelimits.json")

    def tearDown(self):
        shutil.rmtree(self.state, ignore_errors=True)

    def run_feed(self, stdin_text):
        env = dict(os.environ, CLAUDE_USAGE_WIDGET_STATE_DIR=self.state)
        done = subprocess.run([sys.executable, SCRIPT], input=stdin_text, capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def test_writes_every_window_and_prints_remainders_in_a_fixed_order(self):
        resets = int(time.time()) + 3600
        text = self.run_feed(json.dumps({"model": {"display_name": "Opus 5"}, "rate_limits": {
            "seven_day": {"used_percentage": 41.2, "resets_at": resets},
            "five_hour": {"used_percentage": 23.5, "resets_at": resets},
            "seven_day_opus": {"used_percentage": 91, "resets_at": resets}}}))
        self.assertEqual(text, "Opus 5 | 5h 76% left | week 59% left")

        limits = cu.read_rate_limits(self.feed)
        self.assertEqual([w["name"] for w in limits["windows"]], ["five_hour", "seven_day", "seven_day_opus"])
        self.assertTrue(os.path.isfile(os.path.join(self.state, "statusline-last-input.json")))

    def test_session_without_limits_never_blanks_a_good_feed(self):
        self.run_feed(json.dumps({"rate_limits": {"five_hour": {"used_percentage": 5, "resets_at": int(time.time()) + 60}}}))
        with open(self.feed, encoding="utf-8") as handle:
            before = handle.read()
        self.assertEqual(self.run_feed(json.dumps({"model": {"display_name": "Opus 5"}})), "Opus 5")
        with open(self.feed, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), before)

    def test_garbage_input_still_prints_a_status_line(self):
        self.assertEqual(self.run_feed("not json"), "Claude")
        self.assertEqual(self.run_feed(""), "Claude")
        self.assertFalse(os.path.exists(self.feed))


if __name__ == "__main__":
    unittest.main()
