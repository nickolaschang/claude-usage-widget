#!/usr/bin/env python3
"""Claude Code usage engine and command line.

Reads the local Claude Code transcripts (~/.claude/projects/**/*.jsonl), de-duplicates API
responses by message id, prices them with the shared pricing.json, and reports the last
5 hours, today, and the last 7 days. If the status line feed is installed it also reports how
much of each rate limit window is left.

Standard library only. Python 3.9+. Nothing leaves this machine.

    python3 claude_usage.py                     human readable summary
    python3 claude_usage.py --format oneline    "5h 88% . wk 90% left" for polybar and friends
    python3 claude_usage.py --format xbar       SwiftBar / xbar / Argos menu bar plugin output
    python3 claude_usage.py --format waybar     waybar custom module JSON
    python3 claude_usage.py --format json       everything, for scripts
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import deque
from datetime import datetime, timezone

WINDOW_DAYS = 7                      # how far back the engine looks
BIN_SECONDS = 300                    # usage is pre-aggregated into bins this wide
CHUNK_BYTES = 2 * 1024 * 1024        # bytes read per scan step
MAX_LINE_BYTES = 64 * 1024 * 1024    # give up on a single line longer than this
FEED_MAX_AGE_HOURS = 192             # ignore a rate limit feed older than this (8 days)
FEED_STALE_MINUTES = 15              # past this age, say when the limits were last read
LOW_PERCENT = 15                     # a limit with this much or less left counts as low
CACHE_VERSION = 1

DOT = "·"
FALLBACK_MODEL = {"match": "", "label": "Other", "input": 3.0, "output": 15.0, "read_mult": 0.1}


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def projects_root():
    return os.path.join(config_dir(), "projects")


def state_dir():
    """Where the widget keeps its own small files. Same folder as the Windows edition on Windows."""
    override = os.environ.get("CLAUDE_USAGE_WIDGET_STATE_DIR")
    if override:
        return override
    home = os.path.expanduser("~")
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return os.path.join(base, "ClaudeUsageWidget")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "ClaudeUsageWidget")
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state")
    return os.path.join(base, "claude-usage-widget")


def feed_path():
    return os.path.join(state_dir(), "ratelimits.json")


def write_atomic(path, text):
    """Write via a temp file and one rename, so a reader never sees half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def sweep_stale_temp_files(folder, older_than_seconds=60):
    """Claude Code cancels a status line script that is still running when the next update
    arrives, and status bars kill slow commands too. If that lands between writing a temp file
    and renaming it, the temp file is orphaned. Remove any that are clearly no longer in use."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    cutoff = time.time() - older_than_seconds
    for name in names:
        if not name.endswith(".tmp"):
            continue
        path = os.path.join(folder, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

class Pricing:
    """Price table from pricing.json. The first entry whose match text is in the model id wins."""

    def __init__(self, models=None, write_5m=1.25, write_1h=2.0, warning=None, signature=""):
        self.models = list(models or [])
        if not any(m["match"] == "" for m in self.models):
            self.models.append(dict(FALLBACK_MODEL))      # every model must get some rate
        self.write_5m = float(write_5m)
        self.write_1h = float(write_1h)
        self.warning = warning
        self.signature = signature
        self._cache = {}

    def rate_for(self, model_id):
        hit = self._cache.get(model_id)
        if hit is None:
            lowered = model_id.lower()
            hit = next(m for m in self.models if m["match"] in lowered)
            self._cache[model_id] = hit
        return hit


def find_pricing_file():
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(here, "pricing.json"), os.path.join(os.path.dirname(here), "pricing.json")):
        if os.path.isfile(candidate):
            return candidate
    return None


def load_pricing(path=None):
    path = path or find_pricing_file()
    try:
        if not path:
            raise OSError("pricing.json not found next to the script or one folder up")
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        data = json.loads(text)
        models = [{
            "match": str(m["match"]).lower(), "label": str(m["label"]),
            "input": float(m["input"]), "output": float(m["output"]),
            "read_mult": float(m["cache_read_multiplier"]),
        } for m in data["models"]]
        if not models:
            raise ValueError("pricing.json has no models")
        return Pricing(models, data["cache_write_5m_multiplier"], data["cache_write_1h_multiplier"],
                       signature=hashlib.sha256(text.encode("utf-8")).hexdigest())
    except (OSError, ValueError, KeyError, TypeError) as error:
        return Pricing(warning="pricing.json problem, costs are rough (%s)" % error, signature="fallback")


# ---------------------------------------------------------------------------
# Usage engine
# ---------------------------------------------------------------------------

_TIMESTAMP = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.(\d+))?\s*(Z|[+-]\d\d:?\d\d)?$")


def parse_timestamp(text):
    """ISO 8601 to epoch seconds. Tolerant of 'Z' and any number of fraction digits (Python 3.9's
    fromisoformat is not). A value with no zone is taken as UTC."""
    if not isinstance(text, str):
        return None
    found = _TIMESTAMP.match(text.strip())
    if not found:
        return None
    year, month, day, hour, minute, second = (int(found.group(i)) for i in range(1, 7))
    fraction = found.group(7) or ""
    micro = int((fraction + "000000")[:6]) if fraction else 0
    offset = 0
    zone = found.group(8)
    if zone and zone != "Z":
        digits = zone[1:].replace(":", "")
        offset = (1 if zone[0] == "+" else -1) * (int(digits[:2]) * 3600 + int(digits[2:4]) * 60)
    try:
        moment = datetime(year, month, day, hour, minute, second, micro, tzinfo=timezone.utc)
    except ValueError:
        return None
    return moment.timestamp() - offset


def _count(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class UsageEngine:
    """Incremental reader. Call discover() then scan_all() (or scan_step() in slices), then summary()."""

    def __init__(self, root=None, pricing=None, window_days=WINDOW_DAYS):
        self.root = os.path.abspath(root or projects_root())
        self.pricing = pricing or load_pricing()
        self.window_days = window_days
        self.offsets = {}        # path -> bytes consumed
        self.seen = {}           # message id -> [bin key, bin start, cost, in, out, cache write, cache read]
        self.bins = {}           # "<bin start>|<label>" -> totals
        self.last_event = 0.0    # newest response timestamp seen
        self._queue = deque()
        self._queued = set()

    # -- reading ------------------------------------------------------------

    def discover(self, now=None):
        """Queue every transcript touched inside the window that has bytes we have not read."""
        now = time.time() if now is None else now
        cutoff = now - self.window_days * 86400
        for folder, _dirs, names in os.walk(self.root):
            for name in names:
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(folder, name)
                try:
                    info = os.stat(path)
                except OSError:
                    continue
                if info.st_mtime < cutoff or path in self._queued:
                    continue
                if info.st_size != self.offsets.get(path, 0):
                    self._queue.append(path)
                    self._queued.add(path)
        return len(self._queue)

    @property
    def pending(self):
        return len(self._queue)

    def scan_step(self, now=None):
        """Process one chunk of the file at the head of the queue. False once nothing is left."""
        if not self._queue:
            return False
        path = self._queue[0]
        try:
            done = self._read_chunk(path, time.time() if now is None else now)
        except OSError:
            done = True
        if done:
            self._queue.popleft()
            self._queued.discard(path)
        return True

    def scan_all(self, now=None):
        while self.scan_step(now):
            pass

    def _read_chunk(self, path, now):
        offset = self.offsets.get(path, 0)
        with open(path, "rb") as handle:
            length = os.fstat(handle.fileno()).st_size
            if length < offset:
                offset = 0                       # file was rewritten; start over (dedupe keeps this safe)
            if length <= offset:
                self.offsets[path] = offset
                return True
            size = min(length - offset, CHUNK_BYTES)
            while True:
                handle.seek(offset)
                buffer = handle.read(size)
                if not buffer:
                    return True
                last_newline = buffer.rfind(b"\n")
                if last_newline >= 0:
                    break
                if offset + len(buffer) >= length:
                    return True                  # writer is mid-line; try again next refresh
                if size >= MAX_LINE_BYTES:
                    self.offsets[path] = offset + len(buffer)     # absurdly long line; skip past it
                    return False
                size = min(size * 4, length - offset, MAX_LINE_BYTES)

        new_offset = offset + last_newline + 1
        self.offsets[path] = new_offset
        cutoff = now - self.window_days * 86400
        for raw in buffer[:last_newline].split(b"\n"):
            if len(raw) < 80:
                continue
            # Cheap substring checks first; only real candidates pay for a JSON parse.
            if b'"usage":{' not in raw or b'"type":"assistant"' not in raw:
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            self._add_record(record, cutoff)
        return new_offset >= length

    def _add_record(self, record, cutoff):
        if not isinstance(record, dict) or record.get("type") != "assistant":
            return
        message = record.get("message")
        if not isinstance(message, dict):
            return
        usage = message.get("usage")
        model = message.get("model")
        if not isinstance(usage, dict) or not model or model == "<synthetic>":
            return
        stamp = parse_timestamp(record.get("timestamp"))
        if stamp is None or stamp < cutoff:
            return

        # One API response is logged once per content block, and resumed sessions copy old
        # lines into new files. The message id identifies the response across all of them.
        key = message.get("id") or record.get("uuid")
        if not key:
            return

        tokens_in = _count(usage.get("input_tokens"))
        tokens_out = _count(usage.get("output_tokens"))
        cache_read = _count(usage.get("cache_read_input_tokens"))
        creation = usage.get("cache_creation")
        if isinstance(creation, dict):
            write_5m = _count(creation.get("ephemeral_5m_input_tokens"))
            write_1h = _count(creation.get("ephemeral_1h_input_tokens"))
        else:
            write_5m, write_1h = _count(usage.get("cache_creation_input_tokens")), 0

        rate = self.pricing.rate_for(str(model))
        cost = (tokens_in * rate["input"] + tokens_out * rate["output"]
                + write_5m * rate["input"] * self.pricing.write_5m
                + write_1h * rate["input"] * self.pricing.write_1h
                + cache_read * rate["input"] * rate["read_mult"]) / 1e6
        cache_write = write_5m + write_1h

        is_new = True
        old = self.seen.get(key)
        if old is not None:
            # Keep whichever copy reports the most output (guards against partial stream snapshots).
            if old[4] >= tokens_out:
                return
            is_new = False
            old_bin = self.bins.get(old[0])
            if old_bin is not None:
                old_bin["cost"] -= old[2]
                old_bin["input"] -= old[3]
                old_bin["output"] -= old[4]
                old_bin["cache_write"] -= old[5]
                old_bin["cache_read"] -= old[6]
                old_bin["responses"] -= 1

        bin_start = int(stamp // BIN_SECONDS) * BIN_SECONDS
        bin_key = "%d|%s" % (bin_start, rate["label"])
        totals = self.bins.get(bin_key)
        if totals is None:
            totals = self.bins[bin_key] = {"start": bin_start, "model": rate["label"], "cost": 0.0, "input": 0,
                                           "output": 0, "cache_write": 0, "cache_read": 0, "responses": 0}
        totals["cost"] += cost
        totals["input"] += tokens_in
        totals["output"] += tokens_out
        totals["cache_write"] += cache_write
        totals["cache_read"] += cache_read
        totals["responses"] += 1

        self.seen[key] = [bin_key, bin_start, cost, tokens_in, tokens_out, cache_write, cache_read]
        if is_new and stamp > self.last_event:
            self.last_event = stamp

    # -- reporting ----------------------------------------------------------

    def summary(self, now=None):
        now = time.time() if now is None else now
        local = time.localtime(now)
        midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
        cut_7d = now - self.window_days * 86400
        buckets = {}
        for name, cutoff in (("h5", now - 5 * 3600), ("today", midnight), ("d7", cut_7d)):
            buckets[name] = {"cutoff": cutoff, "cost": 0.0, "input": 0, "output": 0, "cache_write": 0,
                             "cache_read": 0, "responses": 0, "by_model": {}}

        for key in [k for k, b in self.bins.items() if b["start"] < cut_7d]:
            del self.bins[key]
        for totals in self.bins.values():
            if totals["responses"] <= 0:
                continue
            for bucket in buckets.values():
                if totals["start"] < bucket["cutoff"]:
                    continue
                for field in ("cost", "input", "output", "cache_write", "cache_read", "responses"):
                    bucket[field] += totals[field]
                bucket["by_model"][totals["model"]] = bucket["by_model"].get(totals["model"], 0.0) + totals["cost"]

        # The dedupe map only needs to remember what is still inside the window.
        for key in [k for k, v in self.seen.items() if v[1] < cut_7d]:
            del self.seen[key]

        for bucket in buckets.values():
            del bucket["cutoff"]
            bucket["tokens"] = bucket["input"] + bucket["output"] + bucket["cache_write"] + bucket["cache_read"]
        return buckets

    # -- cache (lets a status bar call the command every few seconds cheaply) --

    def _signature(self):
        return "%d|%d|%s|%s" % (CACHE_VERSION, self.window_days, self.pricing.signature, self.root)

    def save_cache(self, path):
        payload = {"signature": self._signature(), "offsets": self.offsets, "seen": self.seen,
                   "bins": self.bins, "last_event": self.last_event}
        write_atomic(path, json.dumps(payload, separators=(",", ":")))

    def load_cache(self, path):
        """True if a cache made with the same prices, window and folder was loaded."""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if payload.get("signature") != self._signature():
                return False
            self.offsets = {str(k): int(v) for k, v in payload["offsets"].items()}
            self.seen = {str(k): list(v) for k, v in payload["seen"].items()}
            self.bins = {str(k): dict(v) for k, v in payload["bins"].items()}
            self.last_event = float(payload.get("last_event", 0.0))
            return True
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.offsets, self.seen, self.bins, self.last_event = {}, {}, {}, 0.0
            return False


# ---------------------------------------------------------------------------
# Rate limit feed (written by ratelimit_feed.py or the Windows Write-RateLimitFeed.ps1)
# ---------------------------------------------------------------------------

def limit_label(name):
    known = {"five_hour": "5h limit", "seven_day": "Week", "spend_limit": "Spend"}
    if name in known:
        return known[name]
    if name.startswith("seven_day_"):
        return "Week (%s)" % name[10:].replace("_", " ").title()
    return name.replace("_", " ").title()


def limit_order(name):
    if name == "five_hour":
        return 0
    if name == "seven_day":
        return 1
    return 2 if name.startswith("seven_day_") else 3


def read_rate_limits(path=None, now=None):
    """Feed format (epoch seconds, pct = percent USED):
    {"updated": 0, "windows": {"five_hour": {"pct": 0, "resets": 0}, "seven_day": {...}}}
    Returns None, or {"updated": epoch, "stale": bool, "windows": [ordered list]}."""
    now = time.time() if now is None else now
    try:
        with open(path or feed_path(), "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        updated = float(data["updated"])
        raw_windows = data["windows"]
        if not isinstance(raw_windows, dict):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if now - updated > FEED_MAX_AGE_HOURS * 3600:
        return None

    windows = []
    for name, window in raw_windows.items():
        if not isinstance(window, dict) or window.get("pct") is None:
            continue
        try:
            used = float(window["pct"])
            resets = float(window.get("resets") or 0) or None
        except (TypeError, ValueError):
            continue
        # Once the reset time has passed, the recorded percentage describes a window that is gone.
        if resets is not None and resets <= now:
            used, resets = 0.0, None
        used = max(0.0, min(100.0, used))
        windows.append({"name": name, "label": limit_label(name), "used": used, "left": 100.0 - used,
                        "resets": resets})
    if not windows:
        return None
    windows.sort(key=lambda w: (limit_order(w["name"]), w["name"]))
    return {"updated": updated, "stale": now - updated >= FEED_STALE_MINUTES * 60, "windows": windows}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_tokens(count):
    if count >= 1e9:
        return "%.2fB" % (count / 1e9)
    if count >= 1e6:
        return "%.1fM" % (count / 1e6)
    if count >= 1e3:
        return "%.0fk" % (count / 1e3)
    return "%d" % count


def fmt_cost(cost):
    if cost >= 1000:
        return "${:,.0f}".format(cost)
    if cost >= 100:
        return "$%.0f" % cost
    return "$%.2f" % cost


def fmt_span(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "under 1m"
    if seconds < 3600:
        return "%dm" % (seconds // 60)
    if seconds < 86400:
        return "%dh %02dm" % (seconds // 3600, (seconds % 3600) // 60)
    return "%dd %dh" % (seconds // 86400, (seconds % 86400) // 3600)


def model_split(bucket):
    if bucket["cost"] <= 0:
        return ""
    shares = sorted(bucket["by_model"].items(), key=lambda item: -item[1])
    return "   ".join("%s %.0f%%" % (label, 100 * cost / bucket["cost"])
                      for label, cost in shares if 100 * cost / bucket["cost"] >= 0.5)


def lowest_left(limits):
    return min((w["left"] for w in limits["windows"]), default=100.0) if limits else 100.0


def oneline(summary, limits):
    """The compact pill text: limit remainders when the feed is there, otherwise the cost headline."""
    if limits:
        short = {"five_hour": "5h", "seven_day": "wk"}
        shown = [w for w in limits["windows"] if w["name"] in short] or limits["windows"][:2]
        parts = ["%s %.0f%%" % (short.get(w["name"], w["label"]), w["left"]) for w in shown]
        return (" %s " % DOT).join(parts) + " left"
    return "5h %s %s today %s" % (fmt_cost(summary["h5"]["cost"]), DOT, fmt_cost(summary["today"]["cost"]))


def limit_lines(limits, now=None):
    now = time.time() if now is None else now
    lines = []
    for window in (limits or {}).get("windows", []):
        line = "%s: %.0f%% left" % (window["label"], window["left"])
        if window["resets"]:
            line += ", resets %s" % fmt_span(window["resets"] - now)
        lines.append(line)
    return lines


def usage_lines(summary):
    rows = (("Last 5h", "h5"), ("Today", "today"), ("7 days", "d7"))
    return ["%-8s %9s  %7s tok" % (title, fmt_cost(summary[key]["cost"]), fmt_tokens(summary[key]["tokens"]))
            for title, key in rows]


def render(fmt, summary, limits, engine, now=None):
    now = time.time() if now is None else now
    low = lowest_left(limits) <= LOW_PERCENT

    if fmt == "oneline":
        return oneline(summary, limits)

    if fmt == "waybar":
        tooltip = "\n".join(limit_lines(limits, now) + usage_lines(summary))
        return json.dumps({"text": oneline(summary, limits), "tooltip": tooltip,
                           "class": "low" if low else "ok", "percentage": int(round(lowest_left(limits)))})

    if fmt == "xbar":      # SwiftBar, xbar and Argos all read this format
        lines = [oneline(summary, limits) + (" | color=red" if low else ""), "---"]
        lines += limit_lines(limits, now) + (["---"] if limits else [])
        lines += ["%s | font=Menlo" % row for row in usage_lines(summary)]
        split = model_split(summary["today"])
        lines += ["---", split] if split else []
        lines += ["---", "Refresh | refresh=true"]
        return "\n".join(lines)

    if fmt == "json":
        return json.dumps({"generated": now, "responses": len(engine.seen), "h5": summary["h5"],
                           "today": summary["today"], "d7": summary["d7"], "limits": limits,
                           "pricing_warning": engine.pricing.warning}, indent=2)

    lines = []
    if engine.pricing.warning:
        lines.append("WARNING          : %s" % engine.pricing.warning)
    lines.append("Transcripts root : %s" % engine.root)
    lines.append("Unique responses : {:,}".format(len(engine.seen)))
    for title, key in (("Last 5h", "h5"), ("Today", "today"), ("7 days", "d7")):
        bucket = summary[key]
        lines.append("%-8s %9s  %7s tok  (in %s, out %s, cache write %s, cache read %s)  %s" % (
            title, fmt_cost(bucket["cost"]), fmt_tokens(bucket["tokens"]), fmt_tokens(bucket["input"]),
            fmt_tokens(bucket["output"]), fmt_tokens(bucket["cache_write"]), fmt_tokens(bucket["cache_read"]),
            model_split(bucket)))
    if limits:
        for line in limit_lines(limits, now):
            lines.append("  " + line)
    else:
        lines.append("Rate limit feed  : not installed or stale (see the README)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude Code usage from your local transcripts.")
    parser.add_argument("--format", choices=("summary", "oneline", "xbar", "waybar", "json"), default="summary")
    parser.add_argument("--projects-root", help="transcripts folder (default: ~/.claude/projects)")
    parser.add_argument("--pricing", help="path to pricing.json (default: next to this script, then one folder up)")
    parser.add_argument("--no-cache", action="store_true", help="rescan everything instead of reading only new bytes")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass

    engine = UsageEngine(args.projects_root, load_pricing(args.pricing))
    cache_file = os.path.join(state_dir(), "engine-cache.json")
    if not args.no_cache:
        engine.load_cache(cache_file)
    engine.discover()
    engine.scan_all()
    summary = engine.summary()
    if not args.no_cache:
        try:
            sweep_stale_temp_files(state_dir())
            engine.save_cache(cache_file)
        except OSError:
            pass
    print(render(args.format, summary, read_rate_limits(), engine))
    return 0


if __name__ == "__main__":
    sys.exit(main())
