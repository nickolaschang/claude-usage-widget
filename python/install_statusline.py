#!/usr/bin/env python3
"""Safely adds or removes the widget's status line feed in Claude Code's settings.json.

The 5 hour and weekly limit rows need Claude Code to run ratelimit_feed.py as its status line
command. This script makes that one change to settings.json and nothing else:

  - it never replaces a status line that is not ours unless you pass --force
  - it backs the file up first (settings.json.bak-<timestamp>)
  - it keeps every other setting as it was, in the same order

    python3 install_statusline.py            report what is configured, change nothing
    python3 install_statusline.py install
    python3 install_statusline.py remove

Exit code 0 on success, 2 when it refused to touch someone else's status line, 1 on error.
Standard library only. Python 3.9+.
"""
import argparse
import json
import os
import shutil
import sys
import time

MARKER = "ratelimit_feed.py"


def default_settings_path():
    folder = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(folder, "settings.json")


def wanted_command():
    """Absolute interpreter and script, quoted, so it works whatever PATH Claude Code's shell has.
    Forward slashes everywhere: on Windows the command runs through Git Bash, which eats backslashes."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), MARKER)
    return " ".join('"%s"' % part.replace("\\", "/") for part in (sys.executable or "python3", script))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Add or remove the usage widget's Claude Code status line feed.")
    parser.add_argument("action", nargs="?", choices=("check", "install", "remove"), default="check")
    parser.add_argument("--settings", default=default_settings_path(), help="path to Claude Code's settings.json")
    parser.add_argument("--force", action="store_true", help="with install, replace a status line that is not ours")
    args = parser.parse_args(argv)
    command = wanted_command()

    try:
        settings = {}
        if os.path.isfile(args.settings):
            with open(args.settings, "r", encoding="utf-8-sig") as handle:
                text = handle.read()
            if text.strip():
                settings = json.loads(text)
        if not isinstance(settings, dict):
            raise ValueError("settings.json does not hold a JSON object")

        current = settings.get("statusLine")
        current_command = str(current.get("command", "")) if isinstance(current, dict) else ""
        is_ours = MARKER in current_command

        if args.action == "check":
            if current is None:
                print("status line: none configured")
            elif is_ours and current_command == command:
                print("status line: widget feed installed and up to date")
            elif is_ours:
                print("status line: widget feed installed, but the command differs (moved folder or another Python). "
                      "Run install to fix.")
            else:
                print("status line: belongs to something else: %s" % (current_command or current))
            print("settings   : %s" % args.settings)
            print("wanted     : %s" % command)
            return 0

        if args.action == "install":
            if current is not None and not is_ours and not args.force:
                print("Refusing to replace an existing status line that is not the widget feed:")
                print("  %s" % (current_command or current))
                print("Keep yours and copy the feed-writing part of ratelimit_feed.py into it, or re-run with --force.")
                return 2
            if is_ours and current_command == command:
                print("Already installed, nothing to change.")
                return 0
            settings["statusLine"] = {"type": "command", "command": command, "refreshInterval": 60}
        else:
            if current is None:
                print("No status line configured, nothing to remove.")
                return 0
            if not is_ours:
                print("The configured status line is not the widget feed, leaving it alone.")
                return 2
            del settings["statusLine"]

        os.makedirs(os.path.dirname(os.path.abspath(args.settings)), exist_ok=True)
        if os.path.isfile(args.settings):
            backup = "%s.bak-%s" % (args.settings, time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(args.settings, backup)
            print("Backup     : %s" % backup)
        tmp = "%s.%d.tmp" % (args.settings, os.getpid())
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp, args.settings)
        print("Installed  : %s" % command if args.action == "install" else "Removed the widget status line.")
        print("Settings   : %s" % args.settings)
        return 0
    except (OSError, ValueError) as error:
        print("ERROR: %s" % error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
