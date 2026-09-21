<#
.SYNOPSIS
    Safely adds or removes the widget's status line feed in Claude Code's settings.json.

.DESCRIPTION
    The 5 hour and weekly limit rows need Claude Code to run Write-RateLimitFeed.ps1 as its
    status line command. This script makes that one change to settings.json and nothing else:

      - it never replaces a status line that is not ours unless you pass -Force
      - it backs the file up first (settings.json.bak-<timestamp>)
      - it keeps every other setting as it was
      - it always writes the -ExecutionPolicy Bypass flag, without which Windows PowerShell
        refuses to run the feed script and the limit rows never appear

    Run it like this (the flag is needed for the same reason):
      powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-StatusLine.ps1 -Action Install

    Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts as ANSI.

.PARAMETER Action
    Check (default) reports what is configured and changes nothing. Install adds or updates our
    status line. Remove deletes it, but only if it is ours.

.PARAMETER SettingsPath
    Defaults to $env:CLAUDE_CONFIG_DIR\settings.json or ~\.claude\settings.json.

.PARAMETER Force
    With Install, replace a status line that belongs to something else.

.OUTPUTS
    Exit code 0 on success, 2 when it refused to touch someone else's status line, 1 on error.
#>
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Install', 'Remove')]
    [string]$Action = 'Check',
    [string]$SettingsPath,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$marker = 'Write-RateLimitFeed.ps1'

if (-not $SettingsPath) {
    $configDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.claude' }
    $SettingsPath = Join-Path $configDir 'settings.json'
}

# Forward slashes: Claude Code runs the command through Git Bash, which eats backslashes.
$feedScript = (Join-Path $PSScriptRoot $marker) -replace '\\', '/'
if ($feedScript -match '\s') { $feedScript = '"{0}"' -f $feedScript }
$command = 'powershell -NoProfile -ExecutionPolicy Bypass -File {0}' -f $feedScript

try {
    $settings = $null
    if (Test-Path -LiteralPath $SettingsPath) {
        $text = Get-Content -LiteralPath $SettingsPath -Raw
        if ($text -and $text.Trim()) { $settings = ConvertFrom-Json -InputObject $text }
    }
    if ($null -eq $settings) { $settings = New-Object psobject }
    if ($settings -isnot [psobject] -or $settings -is [array]) { throw 'settings.json does not hold a JSON object' }

    $current = $settings.PSObject.Properties['statusLine']
    $currentCommand = if ($null -ne $current -and $null -ne $current.Value) { [string]$current.Value.command } else { '' }
    $isOurs = $currentCommand -like ('*' + $marker + '*')

    if ($Action -eq 'Check') {
        if ($null -eq $current) { 'status line: none configured' }
        elseif ($isOurs -and $currentCommand -eq $command) { 'status line: widget feed installed and up to date' }
        elseif ($isOurs) { 'status line: widget feed installed, but the command differs (moved folder or missing flag). Run -Action Install to fix.' }
        else { 'status line: belongs to something else: {0}' -f $currentCommand }
        'settings   : {0}' -f $SettingsPath
        'wanted     : {0}' -f $command
        exit 0
    }

    if ($Action -eq 'Install') {
        if ($null -ne $current -and -not $isOurs -and -not $Force) {
            'Refusing to replace an existing status line that is not the widget feed:'
            '  {0}' -f $currentCommand
            'Keep yours and copy the feed-writing part of Write-RateLimitFeed.ps1 into it, or re-run with -Force.'
            exit 2
        }
        if ($isOurs -and $currentCommand -eq $command) { 'Already installed, nothing to change.'; exit 0 }
        $block = [pscustomobject][ordered]@{ type = 'command'; command = $command; refreshInterval = 60 }
        if ($null -ne $current) { $current.Value = $block }
        else { Add-Member -InputObject $settings -MemberType NoteProperty -Name 'statusLine' -Value $block }
    }

    if ($Action -eq 'Remove') {
        if ($null -eq $current) { 'No status line configured, nothing to remove.'; exit 0 }
        if (-not $isOurs) { 'The configured status line is not the widget feed, leaving it alone.'; exit 2 }
        $settings.PSObject.Properties.Remove('statusLine')
    }

    $folder = Split-Path -Parent $SettingsPath
    if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Path $folder -Force) }
    if (Test-Path -LiteralPath $SettingsPath) {
        $backup = '{0}.bak-{1}' -f $SettingsPath, (Get-Date -Format 'yyyyMMdd-HHmmss')
        Copy-Item -LiteralPath $SettingsPath -Destination $backup
        'Backup     : {0}' -f $backup
    }
    # -Depth matters: the default of 2 would silently flatten nested settings.
    $json = ConvertTo-Json -InputObject $settings -Depth 100
    [System.IO.File]::WriteAllText($SettingsPath, $json + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding $false))
    if ($Action -eq 'Install') { 'Installed  : {0}' -f $command } else { 'Removed the widget status line.' }
    'Settings   : {0}' -f $SettingsPath
    exit 0
} catch {
    'ERROR: {0}' -f $_.Exception.Message
    exit 1
}
