---
name: stop
description: Close the floating usage widget for Claude Code. Use when the user asks to stop, close, quit or hide the usage widget.
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/plugin/widget.sh" *)
  - Bash(powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/plugin/widget.ps1" *)
---

# Stop the usage widget

Run the plugin's entry point and tell the user what it printed.

- **macOS, Linux (and Git Bash on Windows):** `sh "${CLAUDE_PLUGIN_ROOT}/plugin/widget.sh" stop`
- **Windows without `sh`:** `powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/plugin/widget.ps1" stop`

This only closes the window. The status line feed, the saved window position and any
"Start with Windows" shortcut stay as they are. The `uninstall` skill removes those.
