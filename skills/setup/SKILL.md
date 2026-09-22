---
name: setup
description: Install the status line feed that lets the usage widget for Claude Code show how much of the 5 hour and weekly limits is left. Changes the user's Claude Code settings.json, so only the user starts it.
disable-model-invocation: true
argument-hint: "[--force]"
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/plugin/widget.sh" *)
  - Bash(powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/plugin/widget.ps1" *)
---

# Set up the limit rows

The 5 hour and weekly percentages are not in the transcripts. The supported local source is the
Claude Code status line, which is handed a `rate_limits` object. This installs a small status line
command that copies those numbers to a local file for the widget.

Arguments from the user: `$ARGUMENTS`

Run the plugin's entry point. **Do not edit `settings.json` yourself.** The script backs the file
up, changes only the `statusLine` key, and leaves every other setting alone.

- **macOS, Linux (and Git Bash on Windows):** `sh "${CLAUDE_PLUGIN_ROOT}/plugin/widget.sh" setup`
- **Windows without `sh`:** `powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/plugin/widget.ps1" setup`

What the exit codes mean:

- `0`: installed, or already installed. Tell the user the limit rows appear within about 30
  seconds of their next Claude Code response **in a terminal session**, and that they need a Pro
  or Max login (API key sessions are not given limit data). The desktop app's chat window and the
  VS Code extension do not run the status line, so a user who lives there needs to open one
  terminal session (the desktop app's built-in terminal counts) and send a message. Mention where
  the backup of `settings.json` was written.
- `2`: the user **already has a status line of their own**, and the script refused to replace it.
  Stop and tell them. They have two choices: keep theirs and copy the feed-writing part of
  `Write-RateLimitFeed.ps1` (Windows) or `ratelimit_feed.py` (macOS, Linux) into it, or replace
  theirs. Only if they clearly ask to replace it, or passed `--force` as the argument, run the
  same command again with `--force` for `widget.sh`, or `-Force` for `widget.ps1`.
- `1`: something went wrong. Show them the error.

Worth telling the user once: the status line command prints a short line in their Claude Code
status bar, such as `Opus 5 | 5h 76% left | week 59% left`. The `uninstall` skill removes it again.
