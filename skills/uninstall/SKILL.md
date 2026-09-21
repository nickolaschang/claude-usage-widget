---
name: uninstall
description: Remove the usage widget for Claude Code from this machine - close it, remove its status line feed and startup shortcut, and delete its app copy. Only the user starts it.
disable-model-invocation: true
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/plugin/widget.sh" *)
  - Bash(powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/plugin/widget.ps1" *)
---

# Uninstall the widget

Run the plugin's entry point and tell the user what it removed.

- **macOS, Linux (and Git Bash on Windows):** `sh "${CLAUDE_PLUGIN_ROOT}/plugin/widget.sh" uninstall`
- **Windows without `sh`:** `powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/plugin/widget.ps1" uninstall`

What it does, in order: closes the widget, removes the `statusLine` block from `settings.json`
(only if it is the widget's own, a status line that belongs to something else is left alone),
removes the "Start with Windows" shortcut on Windows, and deletes the app copy.

What it leaves, and why: the widget's state folder (window position, the limit feed, logs, and
the engine cache) is kept in case the user comes back. The script prints where it is. Offer to
delete it, and only do so if they say yes.

On macOS and Linux, a login item or a menu bar plugin the user set up by hand (LaunchAgent,
autostart `.desktop` file, SwiftBar, waybar, polybar) is theirs to remove. Remind them if they
mention having one.

Finally, the plugin itself is removed with `/plugin uninstall usage-widget`, which the user runs.
