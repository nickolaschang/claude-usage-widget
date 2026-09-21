# Changelog

The version here, the `version` in `.claude-plugin/plugin.json`, and the release tag always match.
Claude Code pins plugin users to that version string, so it is bumped on every release.

## 0.2.0

- **Claude Code plugin.** The repository is now also a plugin and its own marketplace:
  `/plugin marketplace add nickolaschang/claude-usage-widget`, then
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
