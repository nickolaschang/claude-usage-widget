# Notes for AI agents

You are probably here for one of two reasons: a user asked you to set this widget up for them,
or a user asked you to change it. Both are covered below. Humans are welcome to read this too,
it is the shortest accurate description of how the project fits together.

## What this is

A small desktop widget that shows Claude Code usage (API-equivalent cost, tokens, and how much
of the 5 hour and weekly limits is left). Everything is local: it reads the session transcripts
under `~/.claude/projects` and an optional status line feed. It makes no network requests.

| Path | What it is |
| --- | --- |
| `windows/` | Windows edition. PowerShell + WPF, nothing to install. |
| `python/` | Portable edition for macOS and Linux (also runs on Windows). Python 3.9+, standard library only. Floating Tk window plus one-line output for menu bars and status bars. |
| `pricing.json` | The only place prices live. Both editions read it. |
| `python/tests/` | All tests, including a check that the two engines agree. |
| `.claude-plugin/`, `skills/`, `plugin/` | The Claude Code plugin. The repository root is the plugin and its own marketplace. |

## Setting it up for a user

1. **Pick the edition from the OS.** Windows: `windows/`. macOS or Linux: `python/`. Do not
   install both unless asked.
2. **Leave the folder where it is once installed.** The status line setting and any autostart
   entry store absolute paths. If the folder has to move, re-run the installer afterwards.
3. **Check the engine first.** It needs no configuration and proves the transcripts are readable.
   - Windows: `pwsh -NoProfile -File windows/ClaudeUsageWidget.ps1 -SelfTest`
     (or `powershell -NoProfile -ExecutionPolicy Bypass -File ...` if `pwsh` is missing)
   - macOS, Linux: `python3 python/claude_usage.py`

   Zero responses usually means Claude Code keeps its data elsewhere. Check `CLAUDE_CONFIG_DIR`.
4. **Install the status line feed with the installer, not by hand-editing `settings.json`.**
   Run the check first, it changes nothing:
   - Windows: `powershell -NoProfile -ExecutionPolicy Bypass -File windows/Install-StatusLine.ps1 -Action Check`
     then `-Action Install`
   - macOS, Linux: `python3 python/install_statusline.py` then `python3 python/install_statusline.py install`

   Exit code `2` means the user already has a status line of their own. **Stop and ask them.**
   Do not pass `-Force` / `--force` on your own judgement: it replaces something they chose.
   The alternative is to keep their status line and copy the feed-writing part of
   `Write-RateLimitFeed.ps1` or `ratelimit_feed.py` into it.
5. **Start the widget detached.** It is a GUI and does not exit, so do not wait on it.
   - Windows: run `windows/Start-ClaudeUsageWidget.vbs` (for example `wscript.exe <path>`).
   - macOS, Linux: `python3 python/claude_usage_widget.py &` (needs tkinter, see `python/README.md`).
     If there is no desktop session or no tkinter, offer the one-line output instead:
     `python3 python/claude_usage.py --format oneline`.
6. **Confirm the feed.** The limit rows appear once Claude Code has run the status line after a
   response. The state folder then holds `ratelimits.json`:
   Windows `%LOCALAPPDATA%\ClaudeUsageWidget`, macOS `~/Library/Application Support/ClaudeUsageWidget`,
   Linux `${XDG_STATE_HOME:-~/.local/state}/claude-usage-widget`.
   No limits are reported for API key logins, only Pro and Max subscriptions.
7. **Ask before adding autostart.** Recipes are in the edition READMEs.

Things that will waste your time if you do not know them:

- On Windows the status line command must include `-ExecutionPolicy Bypass`. The installer adds
  it. Without it the feed script is refused and nothing is ever written.
- Test a Windows status line command from Git Bash, which is how Claude Code runs it. From
  inside a PowerShell session it always appears to work, because PowerShell hands its own
  execution policy to child processes.
- Use forward slashes in the status line command. Git Bash eats backslashes.
- If no limit rows appear, read `statusline-last-input.json` in the state folder. Missing file:
  the command is not running. File without `rate_limits`: Claude Code is not reporting limits
  for that session.

Privacy rules while you work:

- Do not upload, paste, or quote the user's transcripts or `statusline-last-input.json`. They
  contain working directories, session ids, and conversation content.
- The widget shows the user's real spend and limits. Ask before putting a screenshot or those
  numbers anywhere public, including issues on this repository.

Uninstalling: exit the widget, remove any autostart entry, run the installer with `Remove` /
`remove`, then delete this folder and the state folder.

## The Claude Code plugin

If the user has Claude Code, the plugin is the easiest install: `/plugin marketplace add
nickolaschang/usage-widget`, then `/plugin install usage-widget@usage-widget`. It gives
them `/usage-widget:start`, `stop`, `usage`, `status`, `setup` and `uninstall`.

How it is built, and the rules that keep it working:

- **Skills are thin.** Each `skills/<verb>/SKILL.md` runs `plugin/widget.sh <verb>` (macOS, Linux,
  Git Bash) or `plugin/widget.ps1 <verb>` (Windows) and relays the output. Behaviour lives in those
  two scripts, where it is tested. Do not move logic into the skill text.
- **Nothing persistent may point into the plugin folder.** Claude Code installs a plugin into a
  cache folder named after its version, so `${CLAUDE_PLUGIN_ROOT}` moves on every update and old
  folders are swept after about two weeks. Every verb first copies the app to a stable folder
  (`%LOCALAPPDATA%\ClaudeUsageWidget\app`, `~/Library/Application Support/ClaudeUsageWidget/app`,
  `${XDG_DATA_HOME:-~/.local/share}/claude-usage-widget/app`) and works from there. The status
  line and any startup shortcut must only ever hold that stable path. A test enforces it.
- **A plugin cannot set `statusLine` itself.** Claude Code only honours `agent` and
  `subagentStatusLine` from a plugin's settings. That is why `setup` runs the safe installer.
- **`setup` and `uninstall` are user-only** (`disable-model-invocation: true`), because they change
  the user's settings or delete things. Exit code 2 from `setup` means the user already has a
  status line: stop and ask.
- **Bump `version` in `.claude-plugin/plugin.json` on every release**, to the same number as the
  tag and the changelog. Users are pinned to that string and get no update until it changes. The
  release workflow fails if the tag and the version disagree.
- **Names.** The plugin is `usage-widget`, and the name is immutable once people have it installed.
  Anthropic's terms allow describing what a product works with ("for Claude Code") but not using
  "Claude" or "Anthropic" inside the product, plugin or marketplace name. A test enforces it.
- Only `plugin.json` and `marketplace.json` live in `.claude-plugin/`. Contributor instructions
  live in `.claude/CLAUDE.md`, because a root `CLAUDE.md` is not loaded for plugin users.
- Check your work: `claude plugin validate . --strict`, the same for
  `.claude-plugin/plugin.json` and `skills`, and try it for real with
  `claude --plugin-dir . -p "/usage-widget:status"`.

## Changing the code

- **Prices go in `pricing.json` only.** The first entry whose `match` text appears in the model
  id wins, so specific entries go above general ones and the empty catch-all stays last. A test
  fails if an entry can never match.
- **There are two engines and they must agree.** `windows/ClaudeUsageWidget.ps1` and
  `python/claude_usage.py` implement the same rules: de-duplicate by message id (keep the copy
  with the most output tokens), 5 minute bins aligned to the Unix epoch, a 7 day window, "today"
  from local midnight. Change both or neither. `test_engine.CrossEdition` runs one fixture
  through both wherever PowerShell 7 is available on Windows, and CI does that on every push.
- **The feed file is a contract between four files**: both feed writers and both readers.
  Format: `{"updated": <epoch>, "windows": {"<name>": {"pct": <percent used>, "resets": <epoch>}}}`.
- **`windows/*.ps1` must stay ASCII-only** and work in Windows PowerShell 5.1 as well as
  PowerShell 7. 5.1 reads BOM-less scripts as ANSI. No external modules.
- **`python/` is standard library only and must run on Python 3.9** (what ships with the macOS
  developer tools). No `match`, no runtime `X | Y` unions, no third-party imports.
- Run the tests before you commit: `python -m unittest discover -s python/tests -v`.
  Add `CUW_GUI_TESTS=1` to also open a real window. CI runs both on Linux, macOS and Windows.
- The macOS and Linux window code cannot be tried on the maintainer's machine. If you change
  it, say so plainly in the commit or pull request and rely on the CI window test.
- Commits: small, one concern each, imperative subject line. No AI co-author or
  "generated with" trailers. Nothing personal in the repository: no usernames, home folder
  paths, machine names, or real usage figures in docs, tests, or screenshots. Docs use
  placeholder paths such as `C:/Tools/usage-widget`.
- State files (`state.json`, `ratelimits.json`, `engine-cache.json`, `widget.log`,
  `statusline-last-input.json`) live in the state folder, never in the repository.
