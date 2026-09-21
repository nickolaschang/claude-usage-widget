# Claude usage widget

[![tests](https://github.com/nickolaschang/claude-usage-widget/actions/workflows/tests.yml/badge.svg)](https://github.com/nickolaschang/claude-usage-widget/actions/workflows/tests.yml)

A tiny always-on-top desktop widget that shows how much Claude Code you have been using, and how
much of your 5 hour and weekly limits you have left. Fully local: it reads the files Claude Code
already keeps on your disk and makes no network requests.

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

## Pick your platform

| You are on | Use | Needs | Status |
| --- | --- | --- | --- |
| Windows 10 or 11 | [`windows/`](windows/README.md) | Nothing. PowerShell is built in. | Stable, used daily |
| macOS | [`python/`](python/README.md) | Python 3.9+ (3.9 ships with the developer tools) | Beta, testers wanted |
| Linux | [`python/`](python/README.md) | Python 3.9+, `python3-tk` for the floating window | Beta, testers wanted |

Get it either way:

- **Download**: grab the zip for your platform from the
  [latest release](https://github.com/nickolaschang/claude-usage-widget/releases/latest).
- **Clone**: `git clone https://github.com/nickolaschang/claude-usage-widget.git`. It is small,
  you get both editions, and `git pull` updates it.

On macOS and Linux you do not have to use the floating window at all. The same engine prints one
line for [SwiftBar, xbar, Argos, waybar, or polybar](python/README.md#menu-bars-and-status-bars),
which is often the more natural home for a tiny widget on those desktops.

**Using an AI agent to set it up?** Point it at [AGENTS.md](AGENTS.md). It covers choosing the
edition, the safe status line installer, and the mistakes that are easy to make.

## What "beta" means here

The usage engine is tested on Linux, macOS, and Windows on every push, and the two engines
(PowerShell and Python) are checked against each other so they always give the same numbers.
CI also opens a real window on all three systems. What nobody has checked yet is how the Tk
window looks and behaves on a real Mac or Linux desktop: always-on-top, dragging, and
borderless windows vary a lot between window managers, and Wayland restricts some of it. If you
try it, please open an issue and say what you saw, good or bad.

## Where the numbers come from

Claude Code writes every session to `~/.claude/projects/**/*.jsonl`, including sub-agent and
workflow transcripts in nested `subagents` folders. Each assistant line carries the API `usage`
block. The widget:

1. Scans files touched in the last 7 days, then reads only newly appended bytes every 30 seconds.
2. De-duplicates by message id. One API response is logged once per content block (up to 4 lines
   with identical usage), and resumed sessions copy old lines into new files. Without this the
   totals would be inflated several times over.
3. Prices each response at API list rates from [`pricing.json`](pricing.json), including the
   cache tiers (5 minute write 1.25x input, 1 hour write 2x input, cache read 0.1x input, or
   0.025x on Fable 5.1).

"est. cost" is an API-equivalent figure. On a Pro or Max subscription you are not billed this
amount. It is still the most honest single measure of how hard you are working the models,
because raw token counts are dominated by cheap cache reads. "Last 5h" is a rolling window, not
Anthropic's session window.

Prices were checked on 21 Sep 2026 against https://platform.claude.com/docs/en/about-claude/pricing.
Edit `pricing.json` when prices or models change. Fast mode and the US data residency multiplier
are not modelled. `CLAUDE_CONFIG_DIR` is honoured if you keep your Claude Code data elsewhere.

## Limit remainders

The 5 hour and weekly percentages that `/usage` shows are not in the transcripts. The supported
local source is the Claude Code [status line](https://code.claude.com/docs/en/statusline), which
receives a `rate_limits` object (Pro and Max subscribers, after the first response in a session).
Each edition ships a small status line command that copies those numbers to a local file, and an
installer that adds it to your `settings.json` without touching anything else. The edition
READMEs have the steps.

The numbers only move while a Claude Code session is alive, because that is when the status line
runs. Once the last reading is more than 15 minutes old the widget says so. A window whose reset
time has passed shows as 100% left. Usage from your other devices shows up after the next reading
on this machine.

## Privacy

- No network requests. The widget only reads files on your own disk.
- It parses your transcript lines in memory and keeps only token counts, model names, message
  ids, and timestamps. Prompt and response text is never stored, logged, or displayed.
- It writes only to its own state folder: window position, the limit feed, an error log, an
  engine cache (Python edition), and `statusline-last-input.json`.
- `statusline-last-input.json` is a troubleshooting aid holding the last status line input from
  Claude Code. It includes local metadata such as your working directory and session id, so do
  not paste it into a public bug report without looking at it first.

## License

MIT. See [LICENSE](LICENSE).
