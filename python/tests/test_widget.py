"""Window smoke test. Needs a display, so it only runs when CUW_GUI_TESTS=1
(CI sets it; on Linux CI it runs under xvfb)."""
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import claude_usage as cu  # noqa: E402


def bucket(cost):
    return {"cost": cost, "input": 1200, "output": 480_000, "cache_write": 2_000_000, "cache_read": 140_000_000,
            "responses": 360, "tokens": 142_481_200, "by_model": {"Fable": cost * 0.7, "Opus": cost * 0.3}}


@unittest.skipUnless(os.environ.get("CUW_GUI_TESTS") == "1", "set CUW_GUI_TESTS=1 to open a real window")
class Window(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        import claude_usage_widget as widget
        self.tmp = tempfile.mkdtemp(prefix="cuw-gui-")
        self.state = os.path.join(self.tmp, "state.json")
        self.root = tk.Tk()
        engine = cu.UsageEngine(self.tmp, cu.Pricing())
        self.app = widget.WidgetApp(self.root, engine, start_worker=False, state_file=self.state)
        self.root.update()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def texts(self, parent):
        found = []
        for child in parent.winfo_children():
            if child.winfo_class() == "Label":
                found.append(child.cget("text"))
            found += self.texts(child)
        return found

    def test_render_limits_compact_and_state(self):
        now = time.time()
        summary = {"h5": bucket(77.26), "today": bucket(120.89), "d7": bucket(462.18)}
        limits = {"updated": now, "stale": False, "windows": [
            {"name": "five_hour", "label": "5h limit", "used": 12.0, "left": 88.0, "resets": now + 3 * 3600},
            {"name": "seven_day", "label": "Week", "used": 90.0, "left": 10.0, "resets": now + 5 * 86400}]}

        self.app.render(summary, limits, last_event=now, now=now)
        self.root.update()
        shown = self.texts(self.app.full)
        for expected in ("$77.26", "$121", "$462", "142.5M", "5h limit", "Week", "Fable 70%   Opus 30%"):
            self.assertIn(expected, shown)
        self.assertTrue(any(text.startswith("88% left") for text in shown))
        full_size = (self.root.winfo_reqwidth(), self.root.winfo_reqheight())

        self.app.set_compact(True)
        self.root.update()
        self.assertEqual(self.app.pill_text.cget("text"), "5h 88%% %s wk 10%% left" % cu.DOT)
        self.assertEqual(self.app.pill_text.cget("fg"), "#E5484D", "a limit at 10% left must turn the pill red")
        pill_size = (self.root.winfo_reqwidth(), self.root.winfo_reqheight())
        self.assertLess(pill_size[1], full_size[1] / 2)
        self.assertLess(pill_size[0], full_size[0])

        self.app.set_compact(False)
        self.root.update()
        self.assertEqual((self.root.winfo_reqwidth(), self.root.winfo_reqheight()), full_size)

        self.app.render(summary, None, last_event=0, now=now)          # feed gone: limit rows disappear
        self.root.update()
        self.assertNotIn("5h limit", self.texts(self.app.full))
        self.assertEqual(self.app.pill_text.cget("text"), "5h $77.26 %s today $121" % cu.DOT)

        self.app.set_compact(True)
        with open(self.state, encoding="utf-8") as handle:
            self.assertIn('"compact": true', handle.read())


if __name__ == "__main__":
    unittest.main()
