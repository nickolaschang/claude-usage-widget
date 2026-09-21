<#
.SYNOPSIS
    Claude Code status line command that also feeds the usage widget.

.DESCRIPTION
    Claude Code pipes session JSON to this script on stdin. For Pro/Max subscribers that JSON
    carries a rate_limits object once the session has had its first API response. Documented
    windows are five_hour and seven_day (used_percentage 0-100, resets_at in Unix epoch seconds);
    each window can be absent. This script copies every window it finds into
    %LOCALAPPDATA%\ClaudeUsageWidget\ratelimits.json for the widget, then prints a one-line
    status for the Claude Code status bar.

    Feed format (epoch seconds, pct = percent USED):
      { "updated": 0, "windows": { "five_hour": { "pct": 0, "resets": 0 }, "seven_day": { ... } } }

    Docs: https://code.claude.com/docs/en/statusline

    Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts as ANSI.
#>

$ErrorActionPreference = 'SilentlyContinue'

$raw = [Console]::In.ReadToEnd()
$j = $null
try { $j = ConvertFrom-Json -InputObject $raw } catch { }

$dir = Join-Path $env:LOCALAPPDATA 'ClaudeUsageWidget'
if (-not (Test-Path -LiteralPath $dir)) { [void](New-Item -ItemType Directory -Path $dir -Force) }

# Claude Code cancels a status line script that is still running when the next update arrives.
# If that lands between writing the temp file and renaming it, the temp file is orphaned, so
# sweep up any that are clearly not in use any more.
try {
    $staleBefore = (Get-Date).AddMinutes(-1)
    Get-ChildItem -LiteralPath $dir -Filter 'ratelimits.*.tmp' -File |
        Where-Object { $_.LastWriteTime -lt $staleBefore } |
        Remove-Item -Force
} catch { }

# Diagnostics: keep what Claude Code sent on the most recent run (overwritten every time).
# If this file never appears, the status line command is not being run at all. If it appears
# without a rate_limits object, Claude Code is not reporting limits for this session.
try { [System.IO.File]::WriteAllText((Join-Path $dir 'statusline-last-input.json'), $raw) } catch { }

$parts = @()
if ($null -ne $j) {
    if ($j.model.display_name) { $parts += [string]$j.model.display_name }

    $windows = @{}
    if ($null -ne $j.rate_limits) {
        foreach ($prop in $j.rate_limits.PSObject.Properties) {
            $w = $prop.Value
            if ($null -eq $w -or $null -eq $w.used_percentage) { continue }
            $windows[$prop.Name] = @{ pct = [double]$w.used_percentage; resets = [long]$w.resets_at }
        }
    }

    # Status bar text: the two documented windows, always in the same order.
    foreach ($pair in @(@('five_hour', '5h'), @('seven_day', 'week'))) {
        if (-not $windows.ContainsKey($pair[0])) { continue }
        $left = [Math]::Max(0, [Math]::Min(100, 100 - $windows[$pair[0]].pct))
        $parts += ('{0} {1:0}% left' -f $pair[1], $left)
    }

    # Only write when there is real data, so a fresh session (no rate_limits until its first
    # response) never blanks out a good reading from another session.
    if ($windows.Count -gt 0) {
        try {
            $feed = @{ updated = [long]([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()); windows = $windows }
            $dest = Join-Path $dir 'ratelimits.json'
            $tmp  = Join-Path $dir ('ratelimits.{0}.tmp' -f $PID)
            [System.IO.File]::WriteAllText($tmp, ($feed | ConvertTo-Json -Depth 5 -Compress))
            Move-Item -LiteralPath $tmp -Destination $dest -Force     # swap in one step so readers never see half a file
        } catch { }
    }
}

if ($parts.Count -eq 0) { $parts = @('Claude') }
$parts -join ' | '
