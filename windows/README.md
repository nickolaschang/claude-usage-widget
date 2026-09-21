# Windows edition

PowerShell + WPF. Nothing to install and nothing to build. Works with PowerShell 7 (`pwsh`) and
with the Windows PowerShell 5.1 that is built into Windows 10 and 11.

See the [main README](../README.md) for what the numbers mean and the privacy notes.

## Install and run

1. Put the repository (or the Windows release zip) anywhere. A path without spaces keeps things
   simple, for example `C:\Tools\claude-usage-widget`. Keep `pricing.json` one folder above
   `windows\`, as it is in the repository and the zip.
2. Double-click `windows\Start-ClaudeUsageWidget.vbs`. The widget opens with no console window.

Using it:

- Drag anywhere to move it. The position is remembered.
- Double-click to shrink it to the one-line pill, double-click again to expand. The mode is
  remembered. The pill turns red when any limit has 15% or less left, and hovering it shows the
  full summary.
- The widget keeps its nearest screen edges fixed when it changes size, so a pill docked at the
  right edge of the screen stays at the right edge.
- Right-click for Refresh now, Compact, Always on top, Start with Windows, Exit.
- Hover a row for the breakdown (input, output, cache write, cache read, cost per model).
- The dot in the title turns orange while Claude has answered something in the last 2 minutes.

To check the numbers in a console without opening the window:

```powershell
pwsh -NoProfile -File .\windows\ClaudeUsageWidget.ps1 -SelfTest
```

## Limit remainders

Run the installer. It adds one `statusLine` block to `~\.claude\settings.json`, backs the file up
first, leaves every other setting alone, and refuses to replace a status line you already have:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows\Install-StatusLine.ps1 -Action Check
powershell -NoProfile -ExecutionPolicy Bypass -File .\windows\Install-StatusLine.ps1 -Action Install
```

`-Action Remove` takes it out again. What it writes looks like this:

```json
{
  "statusLine": {
    "type": "command",
    "command": "powershell -NoProfile -ExecutionPolicy Bypass -File C:/Tools/claude-usage-widget/windows/Write-RateLimitFeed.ps1",
    "refreshInterval": 60
  }
}
```

Notes:

- `-ExecutionPolicy Bypass` is required. Windows PowerShell's default policy is Restricted, so
  without it the script is refused ("running scripts is disabled on this system") and no feed is
  ever written. The flag only applies to that one process.
- Forward slashes are deliberate. Claude Code runs the command through Git Bash, which eats
  backslashes.
- The script also prints a short status line such as `Fable 5.1 | 5h 76% left | week 59% left`.
- If you already have a `statusLine` command, keep yours and copy the feed-writing part of
  `Write-RateLimitFeed.ps1` into it instead.
- If you move the folder, run the installer again so the path is updated.

The limit rows appear within 30 seconds of your next Claude Code response.

## Troubleshooting

State lives in `%LOCALAPPDATA%\ClaudeUsageWidget`.

- Nothing appears: look at `widget.log` there.
- Footer says "pricing.json problem": the file is missing or not valid JSON. It belongs next to
  the script or one folder up.
- No limit rows: check `statusline-last-input.json`. If that file never appears, the status line
  command is not running (check the path and the `-ExecutionPolicy` flag). If it appears without
  a `rate_limits` object, Claude Code is not reporting limits for that session (no response yet,
  or not a Pro or Max login).
- Test the status line command from Git Bash, not from inside a PowerShell window. A PowerShell
  session passes its own execution policy to child processes, which hides the problem.

## Uninstall

Exit the widget, untick Start with Windows if you enabled it (or delete
`Claude Usage Widget.lnk` from your Startup folder), run the installer with `-Action Remove`,
then delete this folder and `%LOCALAPPDATA%\ClaudeUsageWidget`.

## Files

| File | Purpose |
| --- | --- |
| `ClaudeUsageWidget.ps1` | The widget and the usage engine |
| `Start-ClaudeUsageWidget.vbs` | No-console launcher (pwsh, falls back to Windows PowerShell) |
| `Write-RateLimitFeed.ps1` | Status line command that feeds the limit rows |
| `Install-StatusLine.ps1` | Adds or removes that status line in `settings.json`, safely |
