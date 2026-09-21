<#
.SYNOPSIS
    Entry point for the Claude Code plugin on Windows. The plugin's skills call this and relay
    what it prints. macOS and Linux use widget.sh, which does the same things.

.DESCRIPTION
    Claude Code keeps an installed plugin in a cache folder whose path includes the version, so it
    moves on every update. The widget needs paths that stay put: the status line setting and the
    Start with Windows shortcut both store an absolute path. So nothing runs from the plugin
    folder. Every verb first copies the app to a stable folder and works from there:

        %LOCALAPPDATA%\ClaudeUsageWidget\app

    Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts as ANSI.

.PARAMETER Verb
    start      copy the app into place and open the widget
    stop       close the widget
    status     what is installed, whether it is running, and the state of the limit feed
    usage      print the usage numbers (no window)
    setup      install the status line feed that powers the limit rows. Exit code 2 means the user
               already has a status line of their own: ask them before using -Force
    uninstall  close the widget, remove our status line and startup shortcut, delete the app copy
    sync       only copy the app into place

.PARAMETER Force
    With setup, replace a status line that belongs to something else.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'status', 'usage', 'setup', 'uninstall', 'sync')]
    [string]$Verb = 'status',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$pluginRoot = Split-Path -Parent $PSScriptRoot
$scoped = [bool]$env:CLAUDE_USAGE_WIDGET_APP_DIR            # tests point this at a temp folder
$appDir = if ($scoped) { $env:CLAUDE_USAGE_WIDGET_APP_DIR } else { Join-Path $env:LOCALAPPDATA 'ClaudeUsageWidget\app' }
$stateDir = Join-Path $env:LOCALAPPDATA 'ClaudeUsageWidget'
$widgetScript = Join-Path $appDir 'windows\ClaudeUsageWidget.ps1'
$installer = Join-Path $appDir 'windows\Install-StatusLine.ps1'
$launcher = Join-Path $appDir 'windows\Start-ClaudeUsageWidget.vbs'
$startupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude Usage Widget.lnk'

function Get-PluginVersion {
    try { return [string](ConvertFrom-Json -InputObject (Get-Content -LiteralPath (Join-Path $pluginRoot '.claude-plugin\plugin.json') -Raw)).version }
    catch { return 'unknown' }
}

function Sync-App {
    $target = Join-Path $appDir 'windows'
    if (-not (Test-Path -LiteralPath $target)) { [void](New-Item -ItemType Directory -Path $target -Force) }
    foreach ($name in 'ClaudeUsageWidget.ps1', 'Start-ClaudeUsageWidget.vbs', 'Write-RateLimitFeed.ps1', 'Install-StatusLine.ps1') {
        Copy-Item -LiteralPath (Join-Path $pluginRoot "windows\$name") -Destination (Join-Path $target $name) -Force
    }
    foreach ($name in 'pricing.json', 'LICENSE') {
        Copy-Item -LiteralPath (Join-Path $pluginRoot $name) -Destination (Join-Path $appDir $name) -Force
    }
    Set-Content -LiteralPath (Join-Path $appDir 'VERSION') -Value (Get-PluginVersion) -Encoding ASCII
}

function Get-WidgetProcess {
    # Any copy of the widget counts (a git clone may be running too), unless a test scoped us.
    $all = Get-CimInstance Win32_Process -Filter "Name='pwsh.exe' OR Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like '*ClaudeUsageWidget.ps1*' -and $_.CommandLine -notlike '*-SelfTest*' }
    if ($scoped) { $all = $all | Where-Object { $_.CommandLine -like ('*' + $appDir + '*') } }
    return @($all)
}

function Invoke-Installer([string]$Action) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $installer, '-Action', $Action)
    if ($Force -and $Action -eq 'Install') { $arguments += '-Force' }
    # Out-Host keeps the installer's text out of this function's return value, which must be
    # the exit code and nothing else.
    & powershell.exe @arguments | Out-Host
    return $LASTEXITCODE
}

switch ($Verb) {
    'sync' {
        Sync-App
        'App copied to {0} (version {1}).' -f $appDir, (Get-PluginVersion)
        exit 0
    }

    'start' {
        $running = Get-WidgetProcess
        if ($running.Count -gt 0) {
            'The widget is already running (pid {0}). Nothing to do.' -f $running[0].ProcessId
            exit 0
        }
        Sync-App
        Start-Process -FilePath (Join-Path $env:WINDIR 'System32\wscript.exe') -ArgumentList ('"{0}"' -f $launcher)
        foreach ($attempt in 1..20) {
            Start-Sleep -Milliseconds 500
            $running = Get-WidgetProcess
            if ($running.Count -gt 0) { break }
        }
        if ($running.Count -eq 0) {
            'The widget did not start. Look at {0}' -f (Join-Path $stateDir 'widget.log')
            exit 1
        }
        'Widget started (pid {0}, version {1}).' -f $running[0].ProcessId, (Get-PluginVersion)
        'Drag it anywhere, double-click to shrink it to a pill, right-click for the menu (including Start with Windows).'
        if (-not (Test-Path -LiteralPath (Join-Path $stateDir 'ratelimits.json'))) {
            'The 5 hour and weekly limit rows need the status line feed. Run the setup skill to add it.'
        }
        exit 0
    }

    'stop' {
        $running = Get-WidgetProcess
        if ($running.Count -eq 0) { 'The widget is not running.'; exit 0 }
        foreach ($process in $running) { Stop-Process -Id $process.ProcessId -Force }
        'Widget stopped.'
        exit 0
    }

    'usage' {
        Sync-App
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $widgetScript -SelfTest
        exit $LASTEXITCODE
    }

    'status' {
        'Plugin version : {0}' -f (Get-PluginVersion)
        $installed = if (Test-Path -LiteralPath (Join-Path $appDir 'VERSION')) { (Get-Content -LiteralPath (Join-Path $appDir 'VERSION') -Raw).Trim() } else { 'not copied yet' }
        'App copy       : {0} ({1})' -f $appDir, $installed
        $running = Get-WidgetProcess
        'Widget         : {0}' -f $(if ($running.Count -gt 0) { 'running, pid ' + $running[0].ProcessId } else { 'not running' })
        $feed = Join-Path $stateDir 'ratelimits.json'
        if (Test-Path -LiteralPath $feed) {
            $age = [int]((Get-Date) - (Get-Item -LiteralPath $feed).LastWriteTime).TotalMinutes
            'Limit feed     : last written {0} minute(s) ago' -f $age
        } else {
            'Limit feed     : none yet (run setup, then send one message in Claude Code)'
        }
        if (Test-Path -LiteralPath $installer) { [void](Invoke-Installer 'Check') }
        else { 'status line: unknown until the app is copied (run start or setup)' }
        exit 0
    }

    'setup' {
        Sync-App
        $code = Invoke-Installer 'Install'
        if ($code -eq 0) { 'The limit rows appear within 30 seconds of your next Claude Code response.' }
        exit $code
    }

    'uninstall' {
        $running = Get-WidgetProcess
        foreach ($process in $running) { Stop-Process -Id $process.ProcessId -Force }
        if ($running.Count -gt 0) { 'Widget stopped.' }
        if (Test-Path -LiteralPath $installer) { [void](Invoke-Installer 'Remove') }
        if (-not $scoped -and (Test-Path -LiteralPath $startupLink)) {
            Remove-Item -LiteralPath $startupLink -Force
            'Removed the Start with Windows shortcut.'
        }
        if (Test-Path -LiteralPath $appDir) {
            Remove-Item -LiteralPath $appDir -Recurse -Force
            'Deleted the app copy at {0}.' -f $appDir
        }
        'Window position, the limit feed and logs are still in {0}. Delete that folder too for a clean slate.' -f $stateDir
        'To remove the plugin itself: /plugin uninstall usage-widget'
        exit 0
    }
}
