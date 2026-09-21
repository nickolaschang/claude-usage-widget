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
        """Text of every label that is actually laid out (hidden alternatives are skipped)."""
        found = []
        for child in parent.winfo_children():
            if not child.winfo_manager():
                continue
            if child.winfo_class() == "Label" and child.cget("text"):        # a ring is an image-only label
                found.append(child.cget("text"))
            found += self.texts(child)
        return found

    def test_shrinking_a_card_that_hangs_off_the_screen_keeps_the_pill_visible(self):
        # Found by a user: drag the card so its bottom edge is past the screen, double-click, and
        # the pill (anchored to that same bottom edge) ended up entirely off-screen.
        now = time.time()
        summary = {"h5": bucket(1.0), "today": bucket(2.0), "d7": bucket(3.0)}
        self.app.render(summary, None, last_event=now, now=now)
        screen_width, screen_height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry("+%d+%d" % (screen_width - 60, screen_height - 60))     # mostly past the corner
        self.root.update()

        self.app.set_compact(True)
        self.root.update()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + self.root.winfo_width(), screen_width)
        self.assertLessEqual(y + self.root.winfo_height(), screen_height)

    @unittest.skipUnless(os.name == "nt", "the taskbar guard is Windows only")
    def test_taskbar_guard_only_polls_fast_outside_the_work_area(self):
        import claude_usage_widget as widget
        now = time.time()
        self.app.render({"h5": bucket(1.0), "today": bucket(2.0), "d7": bucket(3.0)}, None, last_event=now, now=now)
        self.root.geometry("+200+200")                                   # comfortably inside the work area
        self.root.update()
        self.assertFalse(widget.reclaim_from_taskbar(self.root.winfo_id()))
        self.root.geometry("+200+%d" % (self.root.winfo_screenheight() - 10))     # hanging off the bottom edge
        self.root.update()
        self.assertTrue(widget.reclaim_from_taskbar(self.root.winfo_id()))

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

        # Every percentage has a ring gauge next to it, heading for the share that is left.
        for name, left in (("five_hour", 0.88), ("seven_day", 0.10)):
            ring, bar = self.app._rows[name]["gauges"]
            self.assertAlmostEqual(ring.target, left)
            self.assertAlmostEqual(bar.target, left)
            self.assertEqual(ring.low, left <= 0.15)

        self.app.set_compact(True)
        self.root.update()
        self.assertEqual(self.texts(self.app.pill), ["5h 88%", "wk 10%", "left"])
        week = self.app._pill_parts["seven_day"]
        self.assertEqual(week["label"].cget("fg"), "#E5484D", "a limit at 10% left must turn red")
        self.assertTrue(week["gauges"][0].low)
        self.assertFalse(self.app._pill_parts["five_hour"]["gauges"][0].low)
        self.assertAlmostEqual(week["gauges"][0].target, 0.10)

        # The sweep is an animation: let it run and check it lands exactly on the target.
        deadline = time.time() + 3
        while time.time() < deadline and abs(week["gauges"][0].shown - 0.10) > 1e-6:
            self.root.update()
            time.sleep(0.01)
        self.assertAlmostEqual(week["gauges"][0].shown, 0.10, places=5)
        pill_size = (self.root.winfo_reqwidth(), self.root.winfo_reqheight())
        self.assertLess(pill_size[1], full_size[1] / 2)
        self.assertLess(pill_size[0], full_size[0])

        self.app.set_compact(False)
        self.root.update()
        self.assertEqual((self.root.winfo_reqwidth(), self.root.winfo_reqheight()), full_size)

        self.app.render(summary, None, last_event=0, now=now)          # feed gone: limit rows disappear
        self.root.update()
        self.assertNotIn("5h limit", self.texts(self.app.full))
        self.assertEqual(self.app._rows, {})
        self.app.set_compact(True)
        self.root.update()
        self.assertEqual(self.texts(self.app.pill), ["5h $77.26 %s today $121" % cu.DOT], "no feed: cost headline, no rings")
        self.app.set_compact(False)

        self.app.set_compact(True)
        with open(self.state, encoding="utf-8") as handle:
            self.assertIn('"compact": true', handle.read())


if __name__ == "__main__":
    unittest.main()
