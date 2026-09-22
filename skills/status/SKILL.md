---
name: status
description: Check the health of the usage widget for Claude Code - whether it is running, which version is installed, whether the status line feed for the limit rows is set up, and how fresh the limit data is. Use when the widget is missing, the limit rows do not show, or the user asks if it is working.
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/plugin/widget.sh" *)
  - Bash(powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/plugin/widget.ps1" *)
---

# Widget status

Run the plugin's entry point and explain what it printed. It changes nothing.

- **macOS, Linux (and Git Bash on Windows):** `sh "${CLAUDE_PLUGIN_ROOT}/plugin/widget.sh" status`
- **Windows without `sh`:** `powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/plugin/widget.ps1" status`

How to read it:

- **Widget: not running** - offer the `start` skill.
- **App copy: not copied yet** - nothing has been started on this machine yet. `start` does it.
- **App copy version differs from the plugin version** - the plugin was updated. Running `start`
  (after `stop`) refreshes the copy.
- **status line: none configured** - the 5 hour and weekly limit rows cannot show. The user can
  run the `setup` skill.
- **status line: belongs to something else** - the user has their own status line. `setup` will
  refuse to replace it. They can keep theirs and copy the feed-writing part of
  `Write-RateLimitFeed.ps1` or `ratelimit_feed.py` into it.
- **status line: installed, but the command differs** - the app folder moved. `setup` fixes it.
- **Limit feed: none yet, even though the status line is installed** - the feed is written after
  the next Claude Code response, and only for Pro and Max logins. API key sessions are not given
  limit data. The widget's state folder holds `statusline-last-input.json`, the last thing Claude
  Code sent: if that file is missing, the status line command is not running; if it is there
  without a `rate_limits` object, Claude Code is not reporting limits for this session. That file
  contains local paths and the session id, so do not paste it anywhere public.
- **The user works in the Claude Code desktop app's chat window or the VS Code extension** - those
  do not run the status line, so the feed is never written there. The cost rows still work (local
  desktop app sessions write transcripts to the same folder). To get the limit rows, they open one
  terminal session (`claude` in any terminal, or the desktop app's built-in terminal) and send a
  message; the widget then keeps that reading until the next terminal session refreshes it.

If the widget is running but the user cannot see it, it may be parked on another monitor or
behind something: `stop` then `start` brings it back on screen.
