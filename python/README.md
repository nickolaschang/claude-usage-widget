# Portable edition (macOS and Linux)

Python 3.9 or newer, standard library only. No `pip install`. It also runs on Windows, but
Windows users are better served by the [Windows edition](../windows/README.md), which needs
nothing installed.

**Beta.** The engine is tested on macOS, Linux, and Windows on every push, and CI opens a real
window on all three. How the floating window looks and behaves on a real Mac or Linux desktop has
not been checked by a person yet. Please open an issue with what you see.

See the [main README](../README.md) for what the numbers mean and the privacy notes.

## Quick start

```sh
git clone https://github.com/nickolaschang/usage-widget.git
cd usage-widget

python3 python/claude_usage.py              # numbers in the terminal, proves it can read your data
python3 python/claude_usage_widget.py &     # the floating window
```

Keep `pricing.json` one folder above `python/`, as it is in the repository and the release zip.

The floating window needs tkinter:

| System | Get tkinter with |
| --- | --- |
| Debian, Ubuntu, Mint | `sudo apt install python3-tk` |
| Fedora | `sudo dnf install python3-tkinter` |
| Arch | `sudo pacman -S tk` |
| macOS with Homebrew Python | `brew install python-tk` |
| macOS with python.org Python | already included |

Using the window: drag to move, double-click to shrink to a one-line pill and again to expand,
right-click (or Control-click on a Mac) for Refresh now, Compact, Always on top, Quit. Hover a
row for the breakdown. Every limit has a small ring gauge next to its percentage: the orange part
is what you have left, it sweeps in smoothly when it changes, and it turns red and slowly pulses
at 15% or less. Position and mode are remembered, and the widget keeps its nearest screen
edges fixed when it changes size.

If your window manager mishandles the borderless window (no way to move it, not staying on top,
stuck on one workspace), run it with a normal frame instead:

```sh
python3 python/claude_usage_widget.py --decorated
```

On Wayland, applications are not allowed to position themselves or force always-on-top. The
widget runs through XWayland where available, but a status bar module (next section) is usually
the better fit there.

## Menu bars and status bars

The same engine prints output for the common bar hosts. Calls are cheap: it caches what it has
already read and only parses newly appended transcript bytes (about 0.2 seconds per call).

**macOS: SwiftBar or xbar.** Save this in your plugins folder as `claude-usage.30s.sh` and make
it executable (`chmod +x`). The `30s` in the name is the refresh interval.

```sh
#!/bin/sh
exec python3 /path/to/usage-widget/python/claude_usage.py --format xbar
```

**GNOME: Argos.** Same script, saved in `~/.config/argos/`.

**waybar.** The module gets the class `low` when any limit has 15% or less left.

```json
"custom/claude": {
    "exec": "python3 /path/to/usage-widget/python/claude_usage.py --format waybar",
    "return-type": "json",
    "interval": 30
}
```

```css
#custom-claude.low { color: #e5484d; }
```

**polybar.**

```ini
[module/claude]
type = custom/script
exec = python3 /path/to/usage-widget/python/claude_usage.py --format oneline
interval = 30
```

All formats: `summary` (default), `oneline`, `xbar`, `waybar`, `json`. Add `--no-cache` to force
a full rescan.

## Limit remainders

Run the installer. It adds one `statusLine` block to `~/.claude/settings.json`, backs the file up
first, leaves every other setting alone, and refuses to replace a status line you already have:

```sh
python3 python/install_statusline.py            # report only, changes nothing
python3 python/install_statusline.py install
python3 python/install_statusline.py remove     # take it out again
```

It writes the absolute path of the Python you ran it with, so it keeps working whatever `PATH`
Claude Code's shell has. If you move the folder or switch Python, run `install` again. If you
already have a status line, keep yours and copy the feed-writing part of `ratelimit_feed.py`
into it.

The limit rows appear within 30 seconds of your next Claude Code response. They need a Pro or
Max login: API key sessions are not given limit data.

## Start at login

Ask yourself whether you want this first. These are the standard recipes for each desktop. They
have not been tried by the maintainer, who is on Windows, so please report back.

macOS, save as `~/Library/LaunchAgents/local.usage-widget.plist`, then run
`launchctl load ~/Library/LaunchAgents/local.usage-widget.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.usage-widget</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/path/to/usage-widget/python/claude_usage_widget.py</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
```

Linux (most desktops), save as `~/.config/autostart/usage-widget.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Usage Widget
Exec=python3 /path/to/usage-widget/python/claude_usage_widget.py
X-GNOME-Autostart-enabled=true
```

## Troubleshooting

State lives in `~/Library/Application Support/ClaudeUsageWidget` on macOS and
`${XDG_STATE_HOME:-~/.local/state}/claude-usage-widget` on Linux. Set
`CLAUDE_USAGE_WIDGET_STATE_DIR` to move it.

- Zero responses: Claude Code may keep its data elsewhere. Set `CLAUDE_CONFIG_DIR`, or pass
  `--projects-root`.
- `ModuleNotFoundError: No module named 'tkinter'`: install it from the table above, or use a
  bar format, which does not need it.
- No limit rows: check `statusline-last-input.json` in the state folder. If that file never
  appears, the status line command is not running. If it appears without a `rate_limits` object,
  Claude Code is not reporting limits for that session.

## Uninstall

Quit the widget, remove any login item or bar plugin, run
`python3 python/install_statusline.py remove`, then delete this folder and the state folder.

## Files

| File | Purpose |
| --- | --- |
| `claude_usage.py` | The usage engine and the command line (all bar formats) |
| `claude_usage_widget.py` | The floating Tk window |
| `ratelimit_feed.py` | Status line command that feeds the limit rows |
| `install_statusline.py` | Adds or removes that status line in `settings.json`, safely |
| `tests/` | `python3 -m unittest discover -s python/tests -v` |
