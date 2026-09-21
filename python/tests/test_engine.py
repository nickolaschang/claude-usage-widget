"""Engine tests. Standard library only:  python -m unittest discover -s python/tests -v"""
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

REPO = os.path.dirname(os.path.dirname(HERE))
PRICING_FILE = os.path.join(REPO, "pricing.json")

# A fixed "now" at local noon, so the today boundary behaves the same in every time zone.
NOW = time.mktime((2026, 3, 10, 12, 0, 0, 0, 0, -1))
HOUR = 3600
DAY = 86400


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)) + ".%03dZ" % int((epoch % 1) * 1000)


def assistant_line(msg_id, epoch, model="claude-opus-5", tokens_in=10, out=100, cache_read=1000,
                   write_5m=0, write_1h=0, legacy_cache_field=None):
    usage = {"input_tokens": tokens_in, "cache_read_input_tokens": cache_read, "output_tokens": out,
             "service_tier": "standard"}
    if legacy_cache_field is None:
        usage["cache_creation_input_tokens"] = write_5m + write_1h
        usage["cache_creation"] = {"ephemeral_5m_input_tokens": write_5m, "ephemeral_1h_input_tokens": write_1h}
    else:
        usage["cache_creation_input_tokens"] = legacy_cache_field
    record = {"parentUuid": None, "isSidechain": False,
              "message": {"model": model, "id": msg_id, "type": "message", "role": "assistant",
                          "content": [{"type": "text", "text": "x" * 40}], "usage": usage},
              "requestId": "req_" + msg_id, "type": "assistant", "uuid": "uuid-" + msg_id, "timestamp": iso(epoch)}
    return json.dumps(record, separators=(",", ":"))


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cuw-test-")
        self.root = os.path.join(self.tmp, "projects")
        os.makedirs(os.path.join(self.root, "proj"))
        self.pricing = cu.load_pricing(PRICING_FILE)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, lines, mode="w", newline_at_end=True):
        path = os.path.join(self.root, "proj", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode, encoding="utf-8", newline="") as handle:
            handle.write("\n".join(lines) + ("\n" if newline_at_end else ""))
        return path

    def engine(self):
        return cu.UsageEngine(self.root, self.pricing)

    def scan(self, engine=None, now=NOW):
        engine = engine or self.engine()
        engine.discover(now)
        engine.scan_all(now)
        return engine, engine.summary(now)


class Dedupe(EngineCase):
    def test_one_response_logged_per_content_block_counts_once(self):
        line = assistant_line("msg_a", NOW - HOUR)
        _, summary = self.scan_lines([line, line, line, line])
        self.assertEqual(summary["d7"]["responses"], 1)
        self.assertEqual(summary["d7"]["output"], 100)

    def test_copy_with_more_output_replaces_the_partial_one(self):
        early = assistant_line("msg_a", NOW - HOUR, out=3)
        final = assistant_line("msg_a", NOW - HOUR, out=500)
        _, summary = self.scan_lines([early, final, early])
        self.assertEqual(summary["d7"]["responses"], 1)
        self.assertEqual(summary["d7"]["output"], 500)

    def test_resumed_session_copies_in_another_file_are_not_double_counted(self):
        self.write("first.jsonl", [assistant_line("msg_a", NOW - 2 * HOUR), assistant_line("msg_b", NOW - HOUR)])
        self.write("resumed.jsonl", [assistant_line("msg_a", NOW - 2 * HOUR), assistant_line("msg_b", NOW - HOUR),
                                     assistant_line("msg_c", NOW - 60)])
        _, summary = self.scan()
        self.assertEqual(summary["d7"]["responses"], 3)

    def test_nested_subagent_transcripts_are_found(self):
        self.write(os.path.join("session", "subagents", "workflows", "wf_1", "agent-x.jsonl"),
                   [assistant_line("msg_sub", NOW - HOUR)])
        _, summary = self.scan()
        self.assertEqual(summary["d7"]["responses"], 1)

    def scan_lines(self, lines):
        self.write("a.jsonl", lines)
        return self.scan()


class Filtering(EngineCase):
    def test_non_assistant_lines_with_a_usage_object_are_ignored(self):
        # Passes both cheap substring checks, but the record itself is a user line (an Agent tool result).
        tool_result = json.dumps({"type": "user", "toolUseResult": {"usage": {"input_tokens": 99999, "output_tokens": 9}},
                                  "nested": {"type": "assistant"}, "pad": "x" * 80,
                                  "message": {"model": "claude-opus-5", "id": "msg_user",
                                              "usage": {"input_tokens": 99999, "output_tokens": 9}},
                                  "timestamp": iso(NOW - HOUR)}, separators=(",", ":"))
        synthetic = assistant_line("msg_err", NOW - HOUR, model="<synthetic>")
        self.write("a.jsonl", [tool_result, synthetic, "not json at all " + "x" * 80, assistant_line("msg_ok", NOW - HOUR)])
        _, summary = self.scan()
        self.assertEqual(summary["d7"]["responses"], 1)
        self.assertEqual(summary["d7"]["input"], 10)

    def test_time_windows(self):
        self.write("a.jsonl", [
            assistant_line("msg_1h", NOW - HOUR),             # inside 5h, today, 7d
            assistant_line("msg_8h", NOW - 8 * HOUR),         # today (04:00 local) and 7d
            assistant_line("msg_3d", NOW - 3 * DAY),          # 7d only
            assistant_line("msg_9d", NOW - 9 * DAY),          # outside the window
        ])
        _, summary = self.scan()
        self.assertEqual([summary[k]["responses"] for k in ("h5", "today", "d7")], [1, 2, 3])


class Pricing(EngineCase):
    def test_cost_uses_every_token_tier(self):
        self.write("a.jsonl", [assistant_line("msg_a", NOW - HOUR, model="claude-opus-5", tokens_in=1_000_000,
                                              out=1_000_000, cache_read=1_000_000, write_5m=1_000_000, write_1h=1_000_000)])
        _, summary = self.scan()
        # opus: 5 in + 25 out + 5*1.25 five minute write + 5*2 one hour write + 5*0.1 read
        self.assertAlmostEqual(summary["d7"]["cost"], 5 + 25 + 6.25 + 10 + 0.5, places=6)
        self.assertEqual(summary["d7"]["by_model"], {"Opus": summary["d7"]["cost"]})

    def test_fable_cache_reads_use_the_lower_multiplier(self):
        self.write("a.jsonl", [assistant_line("msg_a", NOW - HOUR, model="claude-fable-5-1", tokens_in=0, out=0,
                                              cache_read=1_000_000)])
        _, summary = self.scan()
        self.assertAlmostEqual(summary["d7"]["cost"], 10 * 0.025, places=6)

    def test_old_transcripts_without_the_cache_breakdown_count_as_five_minute_writes(self):
        self.write("a.jsonl", [assistant_line("msg_a", NOW - HOUR, model="claude-haiku-4-5", tokens_in=0, out=0,
                                              cache_read=0, legacy_cache_field=1_000_000)])
        _, summary = self.scan()
        self.assertAlmostEqual(summary["d7"]["cost"], 1 * 1.25, places=6)

    def test_specific_match_beats_general_and_unknown_models_get_the_fallback(self):
        self.assertEqual(self.pricing.rate_for("claude-sonnet-4-5")["input"], 3.0)
        self.assertEqual(self.pricing.rate_for("claude-sonnet-5")["input"], 2.0)
        self.assertEqual(self.pricing.rate_for("some-future-model")["label"], "Other")

    def test_shared_pricing_file_is_well_formed(self):
        self.assertIsNone(self.pricing.warning)
        self.assertEqual(self.pricing.models[-1]["match"], "", "the catch-all entry must be last")
        matches = [m["match"] for m in self.pricing.models]
        for index, match in enumerate(matches):
            for earlier in matches[:index]:
                self.assertFalse(earlier and earlier in match and earlier != match,
                                 "'%s' can never match because '%s' comes first" % (match, earlier))

    def test_missing_pricing_file_falls_back_with_a_warning(self):
        pricing = cu.load_pricing(os.path.join(self.tmp, "nope.json"))
        self.assertIn("pricing.json", pricing.warning)
        self.assertEqual(pricing.rate_for("anything")["label"], "Other")


class Incremental(EngineCase):
    def test_only_appended_bytes_are_read_and_a_half_written_line_waits(self):
        path = self.write("a.jsonl", [assistant_line("msg_a", NOW - HOUR)])
        engine, summary = self.scan()
        self.assertEqual(summary["d7"]["responses"], 1)
        first_offset = engine.offsets[path]

        second = assistant_line("msg_b", NOW - 60)
        with open(path, "a", encoding="utf-8", newline="") as handle:
            handle.write(second[:50])                      # writer is mid-line
        _, summary = self.scan(engine)
        self.assertEqual(summary["d7"]["responses"], 1)
        self.assertEqual(engine.offsets[path], first_offset, "must not consume a partial line")

        with open(path, "a", encoding="utf-8", newline="") as handle:
            handle.write(second[50:] + "\n")
        _, summary = self.scan(engine)
        self.assertEqual(summary["d7"]["responses"], 2)

    def test_rewritten_shorter_file_is_reread_without_double_counting(self):
        path = self.write("a.jsonl", [assistant_line("msg_a", NOW - HOUR), assistant_line("msg_b", NOW - HOUR)])
        engine, _ = self.scan()
        self.write("a.jsonl", [assistant_line("msg_a", NOW - HOUR)])
        self.assertLess(os.path.getsize(path), engine.offsets[path])
        _, summary = self.scan(engine)
        self.assertEqual(summary["d7"]["responses"], 2)

    def test_cache_round_trip_then_new_data(self):
        path = self.write("a.jsonl", [assistant_line("msg_a", NOW - HOUR)])
        engine, before = self.scan()
        cache = os.path.join(self.tmp, "cache.json")
        engine.save_cache(cache)

        restored = self.engine()
        self.assertTrue(restored.load_cache(cache))
        _, after = self.scan(restored)
        self.assertEqual(after, before)

        with open(path, "a", encoding="utf-8", newline="") as handle:
            handle.write(assistant_line("msg_a", NOW - HOUR) + "\n" + assistant_line("msg_b", NOW - 60) + "\n")
        _, grown = self.scan(restored)
        self.assertEqual(grown["d7"]["responses"], 2)

    def test_cache_made_with_other_prices_is_rejected(self):
        self.write("a.jsonl", [assistant_line("msg_a", NOW - HOUR)])
        engine, _ = self.scan()
        cache = os.path.join(self.tmp, "cache.json")
        engine.save_cache(cache)
        other = cu.UsageEngine(self.root, cu.Pricing([dict(cu.FALLBACK_MODEL)], signature="different"))
        self.assertFalse(other.load_cache(cache))
        self.assertEqual(other.offsets, {})


class RateLimits(EngineCase):
    def feed(self, windows, updated=None):
        path = os.path.join(self.tmp, "ratelimits.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"updated": NOW - 60 if updated is None else updated, "windows": windows}, handle)
        return path

    def test_order_labels_and_remainders(self):
        path = self.feed({"seven_day_opus": {"pct": 91, "resets": NOW + DAY}, "seven_day": {"pct": 41.2, "resets": NOW + DAY},
                          "five_hour": {"pct": 23.5, "resets": NOW + HOUR}})
        limits = cu.read_rate_limits(path, NOW)
        self.assertEqual([w["label"] for w in limits["windows"]], ["5h limit", "Week", "Week (Opus)"])
        self.assertAlmostEqual(limits["windows"][0]["left"], 76.5)
        self.assertFalse(limits["stale"])
        self.assertAlmostEqual(cu.lowest_left(limits), 9.0)

    def test_window_past_its_reset_shows_as_full(self):
        limits = cu.read_rate_limits(self.feed({"five_hour": {"pct": 80, "resets": NOW - 10}}), NOW)
        self.assertEqual(limits["windows"][0]["left"], 100.0)
        self.assertIsNone(limits["windows"][0]["resets"])

    def test_stale_old_missing_and_broken_feeds(self):
        self.assertTrue(cu.read_rate_limits(self.feed({"five_hour": {"pct": 1, "resets": NOW + HOUR}},
                                                      updated=NOW - 20 * 60), NOW)["stale"])
        self.assertIsNone(cu.read_rate_limits(self.feed({"five_hour": {"pct": 1}}, updated=NOW - 9 * DAY), NOW))
        self.assertIsNone(cu.read_rate_limits(os.path.join(self.tmp, "absent.json"), NOW))
        broken = os.path.join(self.tmp, "broken.json")
        with open(broken, "w") as handle:
            handle.write('{"updated": 1, "wind')
        self.assertIsNone(cu.read_rate_limits(broken, NOW))

    def test_feed_written_by_windows_powershell_with_a_bom_is_readable(self):
        path = os.path.join(self.tmp, "bom.json")
        with open(path, "w", encoding="utf-8-sig") as handle:
            json.dump({"updated": NOW, "windows": {"five_hour": {"pct": 10, "resets": NOW + HOUR}}}, handle)
        self.assertEqual(cu.read_rate_limits(path, NOW)["windows"][0]["left"], 90.0)


class Formatting(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual([cu.fmt_tokens(n) for n in (999, 18_200, 44_900_000, 1_250_000_000)],
                         ["999", "18k", "44.9M", "1.25B"])
        self.assertEqual([cu.fmt_cost(c) for c in (0, 12.4, 214.77, 1234.5)], ["$0.00", "$12.40", "$215", "$1,234"])
        self.assertEqual([cu.fmt_span(s) for s in (20, 125, 2 * 3600 + 600, 3 * 86400 + 4 * 3600)],
                         ["under 1m", "2m", "2h 10m", "3d 4h"])

    def test_timestamps(self):
        self.assertEqual(cu.parse_timestamp("1970-01-01T00:00:00Z"), 0)
        self.assertAlmostEqual(cu.parse_timestamp("2026-08-18T03:19:14.206Z") % 1, 0.206, places=3)
        self.assertEqual(cu.parse_timestamp("1970-01-01T10:00:00+10:00"), 0)
        self.assertEqual(cu.parse_timestamp("1970-01-01T00:00:00.123456789Z") // 1, 0)
        for bad in (None, 5, "", "yesterday", "2026-13-40T00:00:00Z"):
            self.assertIsNone(cu.parse_timestamp(bad))

    def test_outputs(self):
        bucket = {"cost": 12.4, "input": 1, "output": 2, "cache_write": 3, "cache_read": 4, "responses": 1,
                  "tokens": 10, "by_model": {"Opus": 9.3, "Haiku": 3.1}}
        summary = {"h5": bucket, "today": bucket, "d7": bucket}
        limits = {"updated": NOW, "stale": False, "windows": [
            {"name": "five_hour", "label": "5h limit", "used": 90.0, "left": 10.0, "resets": NOW + 3600},
            {"name": "seven_day", "label": "Week", "used": 18.0, "left": 82.0, "resets": None}]}
        engine = cu.UsageEngine(tempfile.gettempdir(), cu.Pricing())
        self.assertEqual(cu.oneline(summary, limits), "5h 10%% %s wk 82%% left" % cu.DOT)
        self.assertEqual(cu.oneline(summary, None), "5h $12.40 %s today $12.40" % cu.DOT)
        self.assertEqual(cu.model_split(bucket), "Opus 75%   Haiku 25%")
        waybar = json.loads(cu.render("waybar", summary, limits, engine, NOW))
        self.assertEqual((waybar["class"], waybar["percentage"]), ("low", 10))
        xbar = cu.render("xbar", summary, limits, engine, NOW).split("\n")
        self.assertTrue(xbar[0].endswith("| color=red"))
        self.assertIn("5h limit: 10% left, resets 1h 00m", xbar)
        self.assertEqual(json.loads(cu.render("json", summary, None, engine, NOW))["limits"], None)


WINDOWS_SCRIPT = os.path.join(REPO, "windows", "ClaudeUsageWidget.ps1")


@unittest.skipUnless(shutil.which("pwsh") and os.name == "nt" and os.path.isfile(WINDOWS_SCRIPT),
                     "needs PowerShell 7 on Windows and the windows/ folder (absent from the macOS and Linux zip)")
class CrossEdition(EngineCase):
    """The Windows edition has its own engine in PowerShell. Both must give the same answer."""

    def test_powershell_and_python_engines_agree(self):
        now = time.time()
        lines = [assistant_line("msg_%d" % i, now - age, model=model, tokens_in=11 * i, out=101 * i,
                                cache_read=1009 * i, write_5m=53 * i, write_1h=211 * i)
                 for i, (age, model) in enumerate([(HOUR, "claude-fable-5-1"), (2 * HOUR, "claude-opus-5"),
                                                   (3 * DAY, "claude-sonnet-5"), (3 * DAY, "claude-haiku-4-5"),
                                                   (4 * DAY, "claude-sonnet-4-5"), (8 * DAY, "claude-opus-5")], start=1)]
        self.write("a.jsonl", lines + lines[:2])
        self.write(os.path.join("s", "subagents", "agent-1.jsonl"), [lines[0], assistant_line("msg_sub", now - HOUR)])

        done = subprocess.run(["pwsh", "-NoProfile", "-File", WINDOWS_SCRIPT, "-SelfTest", "-AsJson", "-ProjectsRoot", self.root,
                               "-PricingPath", PRICING_FILE], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        powershell = json.loads(done.stdout)

        _, python = self.scan(now=now)
        self.assertEqual(python["d7"]["responses"], 6)
        for bucket in ("h5", "today", "d7"):
            for field in ("input", "output", "cache_write", "cache_read", "responses"):
                self.assertEqual(python[bucket][field], powershell[bucket][field], "%s.%s" % (bucket, field))
            self.assertAlmostEqual(python[bucket]["cost"], powershell[bucket]["cost"], places=5)


if __name__ == "__main__":
    unittest.main()
