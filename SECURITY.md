# Security

## What this software touches

The widget runs entirely on your machine and makes no network requests. It reads the session
transcripts Claude Code keeps under `~/.claude/projects`, keeps only token counts, model names,
message ids and timestamps in memory, and writes a few small files to its own state folder. The
optional status line feed adds one `statusLine` entry to Claude Code's `settings.json`, after
backing the file up.

Things worth reporting include: anything that would make it read or write outside those places,
send data anywhere, run something other than its own scripts, damage `settings.json`, or let a
crafted transcript or feed file do harm.

## Reporting a vulnerability

Please report it privately, not in a public issue:

**https://github.com/nickolaschang/usage-widget/security/advisories/new**

(That is the repository's Security tab, then "Report a vulnerability".)

Say what you found, how to reproduce it, and which version or commit you were on. You should get
a first reply within a week. Fixes ship as a new release, and the advisory is published once a
fix is out, with credit to you unless you would rather not be named.

Please do not include your own transcripts or `statusline-last-input.json` in a report. They
contain conversation content, local paths and session ids. A minimal made-up example is better.

## Supported versions

The latest release. There are no long-term support branches.
