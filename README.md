# Claude usage widget

A tiny always-on-top desktop widget for Windows that shows how much Claude Code you have been
using, and how much of your 5 hour and weekly limits you have left.

Pure PowerShell + WPF. Nothing to install, nothing to build, and nothing leaves your machine.

```
 o CLAUDE USAGE               x
              est. cost  tokens
 Last 5h        $12.40    18.2M
 Today          $31.02    44.9M
 7 days        $214.77     310M
 5h limit      57% left . resets 2h 10m
 Week          82% left . resets 3d 4h
 Fable 61%   Opus 35%   Haiku 4%
 updated 14:32:05
```

Double-click it and it shrinks to a one-line pill:

```
 o 5h 57% . wk 82% left
```

This is an unofficial community tool. It is not made by, endorsed by, or affiliated with Anthropic.

## Requirements

- Windows 10 or 11
- [Claude Code](https://code.claude.com/docs) (the widget reads its local session transcripts)
- PowerShell 7 (`pwsh`) or the built-in Windows PowerShell 5.1

## Install and run

1. Download or clone this repository anywhere. A path without spaces keeps the optional
   status line setup simpler, for example `C:\Tools\claude-usage-widget`.
2. Double-click `Start-ClaudeUsageWidget.vbs`. The widget opens with no console window.

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
pwsh -NoProfile -File .\ClaudeUsageWidget.ps1 -SelfTest
```

## Where the numbers come from

Claude Code writes every session to `~\.claude\projects\**\*.jsonl`, including sub-agent and
workflow transcripts in nested `subagents` folders. Each assistant line carries the API `usage`
block. The widget:

1. Scans files touched in the last 7 days, then tails only newly appended bytes every 30 seconds.
2. De-duplicates by message id. One API response is logged once per content block (up to 4 lines
   with identical usage), and resumed sessions copy old lines into new files. Without this the
   totals would be inflated several times over.
3. Prices each response at API list rates, including the cache tiers (5 minute write 1.25x input,
   1 hour write 2x input, cache read 0.1x input, or 0.025x on Fable 5.1).

"est. cost" is an API-equivalent figure. On a Pro or Max subscription you are not billed this
amount. It is still the most honest single measure of how hard you are working the models,
because raw token counts are dominated by cheap cache reads. "Last 5h" is a rolling window, not
Anthropic's session window.

Prices live in the `$script:Pricing` table at the top of `ClaudeUsageWidget.ps1`. They were
checked on 21 Sep 2026 against https://platform.claude.com/docs/en/about-claude/pricing. Update
the table when prices or models change. Fast mode and the US data residency multiplier are not
modelled. `CLAUDE_CONFIG_DIR` is honoured if you keep your Claude Code data somewhere else.

## Limit remainders (5 hour and weekly)

The 5 hour and weekly percentages that `/usage` shows are not in the transcripts. The supported
local source is the Claude Code [status line](https://code.claude.com/docs/en/statusline), which
receives a `rate_limits` object (Pro and Max subscribers, after the first response in a session).

`Write-RateLimitFeed.ps1` is a status line command that copies every window it is given to
`%LOCALAPPDATA%\ClaudeUsageWidget\ratelimits.json`. The widget draws one row per window showing
what is left ("82% left . resets 3d 4h") with a bar that drains as the allowance is used and
turns red for the last 15%. The documented windows are `five_hour` and `seven_day`. If Claude
Code reports more, they appear as extra rows automatically.

To turn it on, add a `statusLine` block to `~\.claude\settings.json`, pointing at wherever you
put this folder:

```json
{
  "statusLine": {
    "type": "command",
    "command": "powershell -NoProfile -ExecutionPolicy Bypass -File C:/Tools/claude-usage-widget/Write-RateLimitFeed.ps1",
    "refreshInterval": 60
  }
}
```

Notes:

- Use forward slashes in the path. With Git Bash installed, backslashes get eaten as escapes.
  If your path contains spaces, wrap it in escaped quotes (`\"C:/My Tools/.../Write-RateLimitFeed.ps1\"`).
- `-ExecutionPolicy Bypass` is required. Windows PowerShell's default policy is Restricted, so
  without it the script is refused ("running scripts is disabled on this system") and no feed is
  ever written. The flag only applies to that one process.
- The script also prints a short status line such as `Fable 5.1 | 5h 76% left | week 59% left`.
- If you already have a `statusLine` command, keep yours and copy the feed-writing part of
  `Write-RateLimitFeed.ps1` into it instead.

The numbers only move while a Claude Code session is alive, because that is when the status line
runs. Once the last reading is more than 15 minutes old the widget adds a "limits as of HH:mm"
note. A window whose reset time has passed shows as 100% left, and a feed older than 8 days is
ignored. Usage from your other devices only shows up after the next reading on this PC.

## Privacy

- The widget makes no network requests. It only reads files on your own disk.
- It parses your transcript lines in memory and keeps only token counts, model names, message
  ids, and timestamps. Prompt and response text is never stored, logged, or displayed.
- It writes to `%LOCALAPPDATA%\ClaudeUsageWidget` only: `state.json` (window position and mode),
  `ratelimits.json` (the limit feed), `widget.log` (errors), and `statusline-last-input.json`.
- `statusline-last-input.json` is a troubleshooting aid holding the last status line input from
  Claude Code. It includes local metadata such as your working directory and session id, so
  do not paste it into a public bug report without looking at it first.

## Troubleshooting

- Nothing appears: look at `%LOCALAPPDATA%\ClaudeUsageWidget\widget.log`.
- No limit rows: check `statusline-last-input.json` in the same folder. If that file never
  appears, the status line command is not running (check the path and the `-ExecutionPolicy`
  flag). If it appears without a `rate_limits` object, Claude Code is not reporting limits for
  that session (no response yet, or not a Pro or Max login).
- Test the status line command from Git Bash, not from inside a PowerShell window. A PowerShell
  session passes its own execution policy to child processes, which hides the problem.

## Uninstall

Exit the widget, untick Start with Windows first if you enabled it (or delete
`Claude Usage Widget.lnk` from your Startup folder), remove the `statusLine` block from
`settings.json`, and delete this folder and `%LOCALAPPDATA%\ClaudeUsageWidget`.

## Files

| File | Purpose |
| --- | --- |
| `ClaudeUsageWidget.ps1` | The widget and the usage engine |
| `Start-ClaudeUsageWidget.vbs` | No-console launcher (pwsh, falls back to Windows PowerShell) |
| `Write-RateLimitFeed.ps1` | Optional status line command that feeds the limit rows |

## License

MIT. See [LICENSE](LICENSE).
