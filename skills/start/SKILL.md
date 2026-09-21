---
name: start
description: Open the floating usage widget for Claude Code on the desktop (cost, tokens, and how much of the 5 hour and weekly limits is left). Use when the user asks to start, open, show, launch or bring back the usage widget.
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/plugin/widget.sh" *)
  - Bash(powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/plugin/widget.ps1" *)
---

# Start the usage widget

Run the plugin's entry point for this operating system and tell the user what it printed. It is a
tested script, so do not improvise other steps or launch the widget some other way.

- **macOS, Linux (and Git Bash on Windows):** `sh "${CLAUDE_PLUGIN_ROOT}/plugin/widget.sh" start`
- **Windows without `sh`:** `powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/plugin/widget.ps1" start`

The widget is a desktop window that keeps running. The script starts it detached and returns
within a few seconds, so there is nothing to wait for or monitor afterwards.

What the exit codes mean:

- `0`: started, or it was already running. Pass on the usage tips the script prints.
- `3`: a window cannot open here (no tkinter, or no desktop session such as over SSH). The script
  prints how to fix it. Offer the `usage` skill instead, which prints the numbers in the chat.
- `1`: it failed to start. Show the user the log path or command the script suggests.

If the script says the limit rows need the status line feed, mention the `setup` skill. Do not run
`setup` yourself: it changes the user's Claude Code settings, so they start it.
