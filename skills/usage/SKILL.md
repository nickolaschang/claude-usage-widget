---
name: usage
description: Print the user's Claude Code usage here in the chat, with no window - API-equivalent cost and tokens for the last 5 hours, today and 7 days, the split by model, and how much of the 5 hour and weekly limits is left. Use when the user asks how much Claude they have used, what it cost, or how close they are to their limits.
allowed-tools:
  - Bash(sh "${CLAUDE_PLUGIN_ROOT}/plugin/widget.sh" *)
  - Bash(powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/plugin/widget.ps1" *)
---

# Show usage numbers in the chat

Run the plugin's entry point and present what it printed.

- **macOS, Linux (and Git Bash on Windows):** `sh "${CLAUDE_PLUGIN_ROOT}/plugin/widget.sh" usage`
- **Windows without `sh`:** `powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/plugin/widget.ps1" usage`

The numbers come from the session transcripts Claude Code keeps on this machine. Nothing is sent
anywhere. When you present them:

- Show the three windows (last 5 hours, today, 7 days) as a small table: cost, total tokens, and
  the model split.
- Say once that the cost is an **API-equivalent estimate**. On a Pro or Max subscription the user
  is not billed this amount. It is a measure of how hard the models were worked, and it is more
  honest than raw tokens, which are dominated by cheap cache reads.
- If the output has limit lines ("5h limit: 84% left, resets 2h 29m"), lead with those: they
  are usually what the user cares about most.
- If it says the rate limit feed is not installed, mention that the `setup` skill adds it. Do not
  run `setup` yourself.
- Zero responses usually means Claude Code keeps its data somewhere else: check `CLAUDE_CONFIG_DIR`.

Do not read the transcripts yourself to work the numbers out. One API response is logged several
times and resumed sessions copy old lines, so a naive count is several times too high. The
script de-duplicates by message id.
