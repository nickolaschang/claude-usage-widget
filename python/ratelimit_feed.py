#!/usr/bin/env python3
"""Claude Code status line command that also feeds the usage widget.

Claude Code pipes session JSON to this script on stdin. For Pro and Max subscribers that JSON
carries a rate_limits object once the session has had its first API response. The documented
windows are five_hour and seven_day (used_percentage 0-100, resets_at in Unix epoch seconds) and
each window can be absent. This script copies every window it finds to ratelimits.json in the
widget's state folder, then prints a one-line status for the Claude Code status bar.

Feed format (epoch seconds, pct = percent USED):
    {"updated": 0, "windows": {"five_hour": {"pct": 0, "resets": 0}, "seven_day": {...}}}

Docs: https://code.claude.com/docs/en/statusline
Standard library only. Python 3.9+.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_usage import state_dir, sweep_stale_temp_files, write_atomic  # noqa: E402


def read_stored_windows(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            stored = json.load(handle)["windows"]
        return {name: {"pct": float(w["pct"]), "resets": int(w.get("resets") or 0)}
                for name, w in stored.items() if isinstance(w, dict) and w.get("pct") is not None}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def merge_windows(stored, incoming):
    """Several Claude Code sessions run the status line at once, each sending the limits IT last
    received. An idle session keeps re-sending an older number every refresh, which used to
    overwrite the newer one and make the widget flick back and forth. So merge per window and only
    accept a reading that is at least as new: a later reset time means a new window; the same reset
    time with a higher (or equal) used percentage means a later reading, because usage can only rise
    until the window resets. Returns (merged windows, how many incoming windows were accepted)."""
    merged = dict(stored)
    accepted = 0
    for name, new in incoming.items():
        old = merged.get(name)
        if old is None or new["resets"] > old["resets"] or (new["resets"] == old["resets"] and new["pct"] >= old["pct"]):
            merged[name] = new
            accepted += 1
    return merged, accepted


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = None

    folder = state_dir()
    sweep_stale_temp_files(folder)
    # Diagnostics: keep what Claude Code sent on the most recent run (overwritten every time).
    # If this file never appears, the status line command is not being run at all. If it appears
    # without a rate_limits object, Claude Code is not reporting limits for this session.
    try:
        write_atomic(os.path.join(folder, "statusline-last-input.json"), raw)
    except OSError:
        pass

    parts = []
    if isinstance(data, dict):
        model = data.get("model")
        if isinstance(model, dict) and model.get("display_name"):
            parts.append(str(model["display_name"]))

        windows = {}
        limits = data.get("rate_limits")
        if isinstance(limits, dict):
            for name, window in limits.items():
                if not isinstance(window, dict) or window.get("used_percentage") is None:
                    continue
                try:
                    windows[name] = {"pct": float(window["used_percentage"]), "resets": int(window.get("resets_at") or 0)}
                except (TypeError, ValueError):
                    continue

        # Status bar text: the two documented windows, always in the same order.
        for name, short in (("five_hour", "5h"), ("seven_day", "week")):
            if name in windows:
                parts.append("%s %.0f%% left" % (short, max(0.0, min(100.0, 100.0 - windows[name]["pct"]))))

        # Only write when there is real data, so a fresh session (no rate_limits until its first
        # response) never blanks out a good reading from another session.
        if windows:
            try:
                path = os.path.join(folder, "ratelimits.json")
                merged, accepted = merge_windows(read_stored_windows(path), windows)
                if accepted:
                    write_atomic(path, json.dumps({"updated": int(time.time()), "windows": merged}, separators=(",", ":")))
            except OSError:
                pass

    print(" | ".join(parts) if parts else "Claude")
    return 0


if __name__ == "__main__":
    sys.exit(main())
