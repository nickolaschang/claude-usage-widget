# Changelog

The version here, the `version` in `.claude-plugin/plugin.json`, and the release tag always match.
Claude Code pins plugin users to that version string, so it is bumped on every release.

## 0.2.3

- Documented what works where. The cost and token rows work for every local Claude Code
  session, including the desktop app's chat window. The limit rows need the status line, which
  only terminal sessions run: desktop app and VS Code extension users open one terminal session
  (the desktop app's built-in terminal counts) and send a message, and the widget keeps that
  reading until the next one. Cloud sessions leave nothing on disk and are invisible. The
  `status` and `setup` skills now explain this too.

## 0.2.2

- **Renamed to `usage-widget`.** The repository is now `nickolaschang/usage-widget` (the old
  address redirects), so the plugin, its marketplace and the repository all share one name, and
  none of them uses Anthropic's marks. Anthropic's terms allow describing what a product works
  with ("for Claude Code") but not using "Claude" inside the product's own name. The install
  command is now `/plugin marketplace add nickolaschang/usage-widget`.
- The window title is "Usage Widget", the header reads "CLAUDE CODE USAGE", and release zips are
  named `usage-widget-<platform>-<version>.zip`.
- Windows: the Start with Windows shortcut is now `Usage Widget.lnk`. An existing
  `Claude Usage Widget.lnk` is renamed automatically the next time the widget starts.
- Unchanged on purpose: the state folder, environment variables and script file names, so
  existing settings, window positions and status line paths keep working.

## 0.2.1

- Fixed (plugin, Windows): with exactly one widget running, `status` reported "not running" and
  `start` tried to open a second one.
- Fixed (plugin): `stop` and `uninstall` matched any process that merely mentioned the widget's
  script, so they could have closed an unrelated terminal or editor. They now only match a
  PowerShell or Python that is actually running it. A test with a real decoy process covers this.

## 0.2.0

- **Claude Code plugin.** The repository is now also a plugin and its own marketplace:
  `/plugin marketplace add nickolaschang/usage-widget`, then
  `/plugin install usage-widget@usage-widget`. Skills: `start`, `stop`, `usage` (numbers in the
  chat, no window), `status`, `setup` (the status line feed) and `uninstall`.
- The plugin never runs anything from its own folder, because Claude Code moves that folder on
  every update. It copies the app to a stable folder first, so the status line setting and any
  startup shortcut keep working across updates.
- Added a security policy and this changelog.

## 0.1.5

- Ring gauges next to every percentage, in the card and in the pill: the orange part is what is
  left, with an eased sweep, a soft glow, and a red pulse at 15% or less. Rows are now updated in
  place instead of being rebuilt on every refresh.

## 0.1.4

- A pill parked on the Windows taskbar no longer blinks out: the widget reclaims its place above
  the taskbar within about 45 ms (it used to take up to a second), and only ever acts against
  the taskbar.

## 0.1.3

- Fixed: shrinking a card that hung past the screen edge put the pill entirely off-screen.
- Fixed (Windows): a pill parked on the taskbar disappeared behind it.

## 0.1.2

- Fixed: temp files piling up in the state folder when Claude Code cancelled a status line run.

## 0.1.1

- The test suite skips Windows-edition tests when run from the macOS and Linux zip.

## 0.1.0

- First release: Windows edition (PowerShell + WPF), portable edition (Python + Tk) for macOS and
  Linux with one-line output for menu bars and status bars, shared `pricing.json`, safe status
  line installers, `AGENTS.md`, and tests on Linux, macOS and Windows.
