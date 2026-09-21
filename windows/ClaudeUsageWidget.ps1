<#
.SYNOPSIS
    Tiny always-on-top desktop widget showing Claude Code usage.

.DESCRIPTION
    Reads the local Claude Code transcripts (~/.claude/projects/**/*.jsonl), de-duplicates
    API responses by message id, and shows API-equivalent cost and token volume for the
    last 5 hours, today, and the last 7 days. Nothing leaves this machine.

    Optional: if the status line feed is installed (see README.md), the widget also shows
    the real 5-hour and weekly rate-limit percentages.

    Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts as ANSI.

.PARAMETER SelfTest
    Scan once, print the totals to the console, and exit without opening a window.

.PARAMETER AsJson
    With -SelfTest, print the totals as JSON instead of text (used by the cross-edition tests).

.PARAMETER ProjectsRoot
    Override the transcripts folder. Defaults to $env:CLAUDE_CONFIG_DIR\projects or
    ~\.claude\projects.

.PARAMETER PricingPath
    Override the pricing file. Defaults to pricing.json next to this script, then one folder up.
#>
[CmdletBinding()]
param(
    [switch]$SelfTest,
    [switch]$AsJson,
    [string]$ProjectsRoot,
    [string]$PricingPath
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

$script:WindowDays      = 7          # how far back the widget looks
$script:RefreshSeconds  = 30         # how often to look for new transcript data
$script:ChunkBytes      = 2MB        # bytes read per scan step
$script:MaxLineBytes    = 64MB       # give up on a single line longer than this
$script:BinMinutes      = 5          # usage is pre-aggregated into bins this wide
$script:FeedMaxAgeHours = 192        # ignore a rate-limit feed older than this (8 days: a weekly window stays meaningful until it resets)
$script:FeedStaleMinutes = 15        # past this age the widget says when the limits were last read

if (-not $ProjectsRoot) {
    $configDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.claude' }
    $ProjectsRoot = Join-Path $configDir 'projects'
}
$script:ProjectsRoot = $ProjectsRoot

$script:StateDir  = Join-Path $env:LOCALAPPDATA 'ClaudeUsageWidget'
$script:StatePath = Join-Path $script:StateDir 'state.json'
$script:FeedPath  = Join-Path $script:StateDir 'ratelimits.json'
$script:LogPath   = Join-Path $script:StateDir 'widget.log'
if (-not (Test-Path -LiteralPath $script:StateDir)) {
    [void](New-Item -ItemType Directory -Path $script:StateDir -Force)
}

function Write-WidgetLog([string]$Message) {
    try {
        $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
        Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8
    } catch { }
}

# The launcher hides the console, so anything unhandled has to land in the log to be seen.
trap {
    Write-WidgetLog ('unhandled: {0} (line {1})' -f $_.Exception.Message, $_.InvocationInfo.ScriptLineNumber)
    break
}

# Prices live in pricing.json, shared with the Python edition so the two can never drift apart.
# The first entry whose 'match' text appears in the model id wins; the empty match is the fallback.
$script:PricingWarning = $null
if (-not $PricingPath) {
    $candidates = @((Join-Path $PSScriptRoot 'pricing.json'), (Join-Path (Split-Path -Parent $PSScriptRoot) 'pricing.json'))
    $PricingPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
$script:Pricing = @()
$script:CacheWrite5mMult = 1.25
$script:CacheWrite1hMult = 2.00
try {
    if (-not $PricingPath) { throw 'pricing.json not found next to the script or one folder up' }
    $pricingJson = ConvertFrom-Json -InputObject (Get-Content -LiteralPath $PricingPath -Raw)
    $script:CacheWrite5mMult = [double]$pricingJson.cache_write_5m_multiplier
    $script:CacheWrite1hMult = [double]$pricingJson.cache_write_1h_multiplier
    $script:Pricing = @(foreach ($m in $pricingJson.models) {
        [pscustomobject]@{
            Match = ([string]$m.match).ToLowerInvariant(); Label = [string]$m.label
            In = [double]$m.input; Out = [double]$m.output; ReadMult = [double]$m.cache_read_multiplier
        }
    })
    if ($script:Pricing.Count -eq 0) { throw 'pricing.json has no models' }
} catch {
    $script:PricingWarning = 'pricing.json problem, costs are rough'
    Write-WidgetLog ('pricing: {0}' -f $_.Exception.Message)
    $script:Pricing = @()
}
# Whatever the file says, always end with a catch-all so every model gets a rate.
if (-not ($script:Pricing | Where-Object { $_.Match -eq '' })) {
    $script:Pricing += [pscustomobject]@{ Match = ''; Label = 'Other'; In = 3.0; Out = 15.0; ReadMult = 0.1 }
}

# ---------------------------------------------------------------------------
# Usage engine
# ---------------------------------------------------------------------------

$script:FileOffset   = @{}                                        # path -> bytes consumed
$script:Queued       = @{}                                        # path -> $true while waiting in the queue
$script:Queue        = New-Object 'System.Collections.Generic.Queue[string]'
$script:Seen         = [hashtable]::new([StringComparer]::Ordinal) # message id -> what we counted for it
$script:Bins         = [hashtable]::new([StringComparer]::Ordinal) # "<binTicks>|<label>" -> totals
$script:RateCache    = @{}
$script:LastEventUtc = [datetime]::MinValue
$script:LastFeed     = $null
$script:ScanTotal    = 0
$script:LastSeenPrune = [datetime]::UtcNow
$script:BinTicks     = [TimeSpan]::FromMinutes($script:BinMinutes).Ticks
$script:ParseStyles  = [System.Globalization.DateTimeStyles]'AdjustToUniversal, AssumeUniversal'

function Get-Rate([string]$Model) {
    if ($script:RateCache.ContainsKey($Model)) { return $script:RateCache[$Model] }
    $m = $Model.ToLowerInvariant()
    $hit = $null
    foreach ($p in $script:Pricing) {
        if ($m.Contains($p.Match)) { $hit = $p; break }
    }
    $script:RateCache[$Model] = $hit
    return $hit
}

function Get-TranscriptFiles {
    if (-not (Test-Path -LiteralPath $script:ProjectsRoot)) { return @() }
    try {
        $dir = New-Object System.IO.DirectoryInfo $script:ProjectsRoot
        return @($dir.GetFiles('*.jsonl', [System.IO.SearchOption]::AllDirectories))
    } catch {
        return @(Get-ChildItem -LiteralPath $script:ProjectsRoot -Recurse -File -Filter '*.jsonl' -ErrorAction SilentlyContinue)
    }
}

function Find-ChangedFiles {
    # Queue every transcript that was touched inside the window and has bytes we have not read.
    $cutoff = [datetime]::UtcNow.AddDays(-$script:WindowDays)
    foreach ($f in (Get-TranscriptFiles)) {
        if ($f.LastWriteTimeUtc -lt $cutoff) { continue }
        $path = $f.FullName
        if ($script:Queued.ContainsKey($path)) { continue }
        $known = [long]0
        if ($script:FileOffset.ContainsKey($path)) { $known = [long]$script:FileOffset[$path] }
        if ($f.Length -ne $known) {
            $script:Queue.Enqueue($path)
            $script:Queued[$path] = $true
        }
    }
    $script:ScanTotal = $script:Queue.Count
}

function Add-UsageRecord($o, [datetime]$Cutoff) {
    if ($o.type -ne 'assistant') { return }
    $msg = $o.message
    if ($null -eq $msg) { return }
    $u = $msg.usage
    if ($null -eq $u) { return }
    $model = [string]$msg.model
    if (-not $model -or $model -eq '<synthetic>') { return }

    $ts = $o.timestamp
    if ($null -eq $ts) { return }
    if ($ts -is [datetime]) { $t = $ts.ToUniversalTime() }
    else { $t = [datetime]::Parse([string]$ts, [cultureinfo]::InvariantCulture, $script:ParseStyles) }
    if ($t -lt $Cutoff) { return }

    # One API response is logged once per content block, and resumed sessions copy old
    # lines into new files. The message id identifies the response across all of them.
    $key = [string]$msg.id
    if (-not $key) { $key = [string]$o.uuid }
    if (-not $key) { return }

    $in  = [long]$u.input_tokens
    $out = [long]$u.output_tokens
    $cr  = [long]$u.cache_read_input_tokens
    $cw5 = [long]0
    $cw1h = [long]0
    $cc = $u.cache_creation
    if ($null -ne $cc) {
        $cw5  = [long]$cc.ephemeral_5m_input_tokens
        $cw1h = [long]$cc.ephemeral_1h_input_tokens
    } else {
        $cw5 = [long]$u.cache_creation_input_tokens
    }

    $rate = Get-Rate $model
    $cost = (($in * $rate.In) + ($out * $rate.Out) +
             ($cw5 * $rate.In * $script:CacheWrite5mMult) +
             ($cw1h * $rate.In * $script:CacheWrite1hMult) +
             ($cr * $rate.In * $rate.ReadMult)) / 1e6
    $cw = $cw5 + $cw1h

    $isNew = $true
    $old = $script:Seen[$key]
    if ($null -ne $old) {
        # Keep whichever copy reports the most output (guards against partial stream snapshots).
        if ($old.Out -ge $out) { return }
        $isNew = $false
        $oldBin = $script:Bins[$old.Bin]
        if ($null -ne $oldBin) {
            $oldBin.Cost -= $old.Cost; $oldBin.In -= $old.In; $oldBin.Out -= $old.Out
            $oldBin.CacheW -= $old.CacheW; $oldBin.CacheR -= $old.CacheR; $oldBin.Msgs -= 1
        }
    }

    $binStartTicks = [long]([Math]::Floor($t.Ticks / $script:BinTicks)) * $script:BinTicks
    $binKey = '{0}|{1}' -f $binStartTicks, $rate.Label
    $bin = $script:Bins[$binKey]
    if ($null -eq $bin) {
        $binStart = [datetime]::new([long]$binStartTicks, [System.DateTimeKind]::Utc)
        $bin = @{
            Start = $binStart
            Model = $rate.Label
            Cost = 0.0; In = [long]0; Out = [long]0; CacheW = [long]0; CacheR = [long]0; Msgs = 0
        }
        $script:Bins[$binKey] = $bin
    }
    $bin.Cost += $cost; $bin.In += $in; $bin.Out += $out
    $bin.CacheW += $cw; $bin.CacheR += $cr; $bin.Msgs += 1

    $script:Seen[$key] = @{ Bin = $binKey; Start = $bin.Start; Cost = $cost; In = $in; Out = $out; CacheW = $cw; CacheR = $cr }
    if ($isNew -and $t -gt $script:LastEventUtc) { $script:LastEventUtc = $t }
}

function Read-FileChunk([string]$Path) {
    # Reads the next chunk of whole lines from $Path. Returns $true once the file is caught up.
    $offset = [long]0
    if ($script:FileOffset.ContainsKey($Path)) { $offset = [long]$script:FileOffset[$Path] }

    $share = [System.IO.FileShare]'ReadWrite, Delete'
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $share)
    try {
        $length = $fs.Length
        if ($length -lt $offset) { $offset = 0 }          # file was rewritten; start over (dedupe keeps this safe)
        if ($length -le $offset) {
            $script:FileOffset[$Path] = $offset
            return $true
        }

        $size = [int][Math]::Min($length - $offset, [long]$script:ChunkBytes)
        while ($true) {
            $buf = New-Object byte[] $size
            [void]$fs.Seek($offset, [System.IO.SeekOrigin]::Begin)
            $read = 0
            while ($read -lt $size) {
                $n = $fs.Read($buf, $read, $size - $read)
                if ($n -le 0) { break }
                $read += $n
            }
            if ($read -le 0) { return $true }

            $lastNl = [Array]::LastIndexOf($buf, [byte]10, $read - 1)
            if ($lastNl -ge 0) { break }

            # No newline in this chunk.
            if (($offset + $read) -ge $length) { return $true }     # writer is mid-line; try again next refresh
            if ($size -ge $script:MaxLineBytes) {
                $script:FileOffset[$Path] = $offset + $read          # absurdly long line; skip past it
                return $false
            }
            $size = [int][Math]::Min([long]$size * 4, [Math]::Min($length - $offset, [long]$script:MaxLineBytes))
        }
    } finally {
        $fs.Dispose()
    }

    $newOffset = $offset + $lastNl + 1
    $script:FileOffset[$Path] = $newOffset

    $cutoff = [datetime]::UtcNow.AddDays(-$script:WindowDays)
    $text = [System.Text.Encoding]::UTF8.GetString($buf, 0, $lastNl)
    $ordinal = [System.StringComparison]::Ordinal
    foreach ($line in $text.Split([char]10)) {
        if ($line.Length -lt 80) { continue }
        # Cheap substring checks first; only real candidates pay for a JSON parse.
        if ($line.IndexOf('"usage":{', $ordinal) -lt 0) { continue }
        if ($line.IndexOf('"type":"assistant"', $ordinal) -lt 0) { continue }
        try {
            $o = ConvertFrom-Json -InputObject $line
            Add-UsageRecord $o $cutoff
        } catch { }
    }
    return ($newOffset -ge $length)
}

function Invoke-ScanStep {
    # Processes one chunk of the file at the head of the queue. Returns $false when nothing is left.
    if ($script:Queue.Count -eq 0) { return $false }
    $path = $script:Queue.Peek()
    $done = $true
    try { $done = Read-FileChunk $path }
    catch {
        Write-WidgetLog ("scan failed for {0}: {1}" -f $path, $_.Exception.Message)
        $done = $true
    }
    if ($done) {
        [void]$script:Queue.Dequeue()
        $script:Queued.Remove($path)
    }
    return $true
}

function Get-UsageSummary {
    $now = [datetime]::UtcNow
    $cut7d = $now.AddDays(-$script:WindowDays)
    $buckets = @(
        @{ Name = 'H5';    Cutoff = $now.AddHours(-5) }
        @{ Name = 'Today'; Cutoff = [datetime]::Today.ToUniversalTime() }
        @{ Name = 'D7';    Cutoff = $cut7d }
    )
    foreach ($b in $buckets) {
        $b.Cost = 0.0; $b.In = [long]0; $b.Out = [long]0; $b.CacheW = [long]0; $b.CacheR = [long]0
        $b.Msgs = 0; $b.ByModel = @{}
    }

    $stale = New-Object 'System.Collections.Generic.List[string]'
    foreach ($entry in $script:Bins.GetEnumerator()) {
        $bin = $entry.Value
        if ($bin.Start -lt $cut7d) { $stale.Add([string]$entry.Key); continue }
        if ($bin.Msgs -le 0) { continue }
        foreach ($b in $buckets) {
            if ($bin.Start -lt $b.Cutoff) { continue }
            $b.Cost += $bin.Cost; $b.In += $bin.In; $b.Out += $bin.Out
            $b.CacheW += $bin.CacheW; $b.CacheR += $bin.CacheR; $b.Msgs += $bin.Msgs
            $b.ByModel[$bin.Model] = [double]$b.ByModel[$bin.Model] + $bin.Cost
        }
    }
    foreach ($k in $stale) { $script:Bins.Remove($k) }

    # The dedupe map only needs to remember what is still inside the window.
    if (($now - $script:LastSeenPrune).TotalHours -ge 1) {
        $script:LastSeenPrune = $now
        $old = New-Object 'System.Collections.Generic.List[string]'
        foreach ($entry in $script:Seen.GetEnumerator()) {
            if ($entry.Value.Start -lt $cut7d) { $old.Add([string]$entry.Key) }
        }
        foreach ($k in $old) { $script:Seen.Remove($k) }
    }

    $summary = @{}
    foreach ($b in $buckets) {
        $b.Tokens = $b.In + $b.Out + $b.CacheW + $b.CacheR
        $summary[$b.Name] = $b
    }
    return $summary
}

function Get-LimitLabel([string]$Name) {
    switch ($Name) {
        'five_hour'   { return '5h limit' }
        'seven_day'   { return 'Week' }
        'spend_limit' { return 'Spend' }
    }
    $words = $Name
    $prefix = ''
    if ($Name -like 'seven_day_*') { $words = $Name.Substring(10); $prefix = 'Week' }
    $words = (Get-Culture).TextInfo.ToTitleCase($words.Replace('_', ' '))
    if ($prefix) { return ('{0} ({1})' -f $prefix, $words) }
    return $words
}

function Get-LimitOrder([string]$Name) {
    if ($Name -eq 'five_hour') { return 0 }
    if ($Name -eq 'seven_day') { return 1 }
    if ($Name -like 'seven_day_*') { return 2 }
    return 3
}

function Read-RateLimits {
    # Feed written by Write-RateLimitFeed.ps1 (status line helper). Epoch seconds, pct = percent USED:
    # { "updated": 0, "windows": { "five_hour": { "pct": 0, "resets": 0 }, "seven_day": { ... } } }
    # Returns every window found, ordered 5h, week, other weekly windows, anything else.
    if (-not (Test-Path -LiteralPath $script:FeedPath)) { return $null }
    try {
        $j = ConvertFrom-Json -InputObject (Get-Content -LiteralPath $script:FeedPath -Raw)
        $script:LastFeed = $j
    } catch {
        $j = $script:LastFeed          # caught the file mid-write; reuse the last good read
    }
    if ($null -eq $j -or $null -eq $j.updated -or $null -eq $j.windows) { return $null }

    $epoch = [datetime]::new(1970, 1, 1, 0, 0, 0, [System.DateTimeKind]::Utc)
    $updated = $epoch.AddSeconds([double]$j.updated)
    $now = [datetime]::UtcNow
    if (($now - $updated).TotalHours -gt $script:FeedMaxAgeHours) { return $null }

    $list = New-Object System.Collections.ArrayList
    foreach ($prop in $j.windows.PSObject.Properties) {
        $w = $prop.Value
        if ($null -eq $w -or $null -eq $w.pct) { continue }
        $used = [double]$w.pct
        $resets = $null
        if ($null -ne $w.resets -and [double]$w.resets -gt 0) { $resets = $epoch.AddSeconds([double]$w.resets) }
        # Once the reset time has passed, the recorded percentage describes a window that is gone.
        if ($null -ne $resets -and $resets -le $now) { $used = 0; $resets = $null }
        $used = [Math]::Max(0, [Math]::Min(100, $used))
        [void]$list.Add(@{
            Name = $prop.Name; Label = (Get-LimitLabel $prop.Name); Order = (Get-LimitOrder $prop.Name)
            Used = $used; Left = 100 - $used; Resets = $resets
        })
    }
    if ($list.Count -eq 0) { return $null }
    $sorted = @($list | Sort-Object { $_.Order }, { $_.Name })
    return @{ Updated = $updated; Windows = $sorted }
}

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

function Format-Tokens([double]$n) {
    if ($n -ge 1e9) { return ('{0:0.00}B' -f ($n / 1e9)) }
    if ($n -ge 1e6) { return ('{0:0.0}M' -f ($n / 1e6)) }
    if ($n -ge 1e3) { return ('{0:0}k' -f ($n / 1e3)) }
    return ('{0:0}' -f $n)
}

function Format-Cost([double]$c) {
    if ($c -ge 1000) { return ('${0:N0}' -f $c) }
    if ($c -ge 100)  { return ('${0:0}' -f $c) }
    return ('${0:0.00}' -f $c)
}

function Format-Span([TimeSpan]$span) {
    if ($span.TotalMinutes -lt 1) { return 'under 1m' }
    if ($span.TotalHours -lt 1)   { return ('{0}m' -f [int][Math]::Floor($span.TotalMinutes)) }
    if ($span.TotalHours -lt 24)  { return ('{0}h {1:00}m' -f [int][Math]::Floor($span.TotalHours), $span.Minutes) }
    return ('{0}d {1}h' -f [int][Math]::Floor($span.TotalDays), $span.Hours)
}

function Get-ModelSplit($bucket) {
    if ($bucket.Cost -le 0) { return '' }
    $parts = foreach ($kv in ($bucket.ByModel.GetEnumerator() | Sort-Object Value -Descending)) {
        $share = 100 * $kv.Value / $bucket.Cost
        if ($share -ge 0.5) { '{0} {1:0}%' -f $kv.Key, $share }
    }
    return (@($parts) -join '   ')
}

function Get-BucketTip($bucket) {
    $lines = @(
        ('{0:N0} responses' -f $bucket.Msgs)
        ('input  {0}' -f (Format-Tokens $bucket.In))
        ('output  {0}' -f (Format-Tokens $bucket.Out))
        ('cache write  {0}' -f (Format-Tokens $bucket.CacheW))
        ('cache read  {0}' -f (Format-Tokens $bucket.CacheR))
    )
    foreach ($kv in ($bucket.ByModel.GetEnumerator() | Sort-Object Value -Descending)) {
        $lines += ('{0}  {1}' -f $kv.Key, (Format-Cost $kv.Value))
    }
    return ($lines -join "`n")
}

# ---------------------------------------------------------------------------
# Self test: no window, just numbers
# ---------------------------------------------------------------------------

if ($SelfTest) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Find-ChangedFiles
    $fileCount = $script:Queue.Count
    while (Invoke-ScanStep) { }
    $sw.Stop()
    $s = Get-UsageSummary
    if ($AsJson) {
        # Same shape as the Python edition's --format json, so the two engines can be compared.
        $out = [ordered]@{ responses = $script:Seen.Count }
        foreach ($pair in @(@('H5', 'h5'), @('Today', 'today'), @('D7', 'd7'))) {
            $b = $s[$pair[0]]
            $out[$pair[1]] = [ordered]@{
                cost = [Math]::Round([double]$b.Cost, 6); input = $b.In; output = $b.Out
                cache_write = $b.CacheW; cache_read = $b.CacheR; responses = $b.Msgs
            }
        }
        $out | ConvertTo-Json -Depth 4
        return
    }
    if ($script:PricingWarning) { 'WARNING          : {0}' -f $script:PricingWarning }
    'Transcripts root : {0}' -f $script:ProjectsRoot
    'Files scanned    : {0} in {1:0.0}s' -f $fileCount, $sw.Elapsed.TotalSeconds
    'Unique responses : {0:N0}' -f $script:Seen.Count
    foreach ($name in 'H5', 'Today', 'D7') {
        $b = $s[$name]
        '{0,-6} {1,10}  {2,8} tok  (in {3}, out {4}, cache write {5}, cache read {6})  {7}' -f $name,
            (Format-Cost $b.Cost), (Format-Tokens $b.Tokens), (Format-Tokens $b.In), (Format-Tokens $b.Out),
            (Format-Tokens $b.CacheW), (Format-Tokens $b.CacheR), (Get-ModelSplit $b)
    }
    $rl = Read-RateLimits
    if ($null -eq $rl) { 'Rate-limit feed  : not installed or stale' }
    else {
        'Rate-limit feed  : read {0:HH:mm:ss}' -f $rl.Updated.ToLocalTime()
        foreach ($limit in $rl.Windows) {
            $resetText = if ($null -ne $limit.Resets) { 'resets in ' + (Format-Span ($limit.Resets - [datetime]::UtcNow)) } else { 'no reset time' }
            '  {0,-14} {1,3:0}% left  ({2:0}% used, {3})' -f $limit.Label, $limit.Left, $limit.Used, $resetText
        }
    }
    return
}

# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

# WPF needs a single-threaded apartment. Relaunch with -STA if the host started us otherwise.
if ([System.Threading.Thread]::CurrentThread.GetApartmentState() -ne [System.Threading.ApartmentState]::STA) {
    $exe = (Get-Process -Id $PID).Path
    Start-Process -FilePath $exe -WindowStyle Hidden -ArgumentList @(
        '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath))
    return
}

$createdNew = $false
$script:Mutex = New-Object System.Threading.Mutex($true, 'Local\ClaudeUsageWidget', [ref]$createdNew)
if (-not $createdNew) { return }   # already running

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase

$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Claude Usage" WindowStyle="None" AllowsTransparency="True" Background="Transparent"
        Topmost="True" ShowInTaskbar="False" ResizeMode="NoResize" SizeToContent="WidthAndHeight"
        WindowStartupLocation="Manual" UseLayoutRounding="True" SnapsToDevicePixels="True"
        FontFamily="Segoe UI" FontSize="12" Foreground="#F3F1EA">
  <Window.Resources>
    <Style x:Key="Label" TargetType="TextBlock">
      <Setter Property="Foreground" Value="#B8B5AD"/>
      <Setter Property="Margin" Value="0,2,14,2"/>
      <Setter Property="VerticalAlignment" Value="Center"/>
    </Style>
    <Style x:Key="Value" TargetType="TextBlock">
      <Setter Property="FontFamily" Value="Cascadia Mono, Consolas"/>
      <Setter Property="FontSize" Value="13"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="HorizontalAlignment" Value="Right"/>
      <Setter Property="VerticalAlignment" Value="Center"/>
      <Setter Property="Margin" Value="0,2,0,2"/>
    </Style>
    <Style x:Key="Dim" TargetType="TextBlock">
      <Setter Property="FontFamily" Value="Cascadia Mono, Consolas"/>
      <Setter Property="FontSize" Value="11"/>
      <Setter Property="Foreground" Value="#8A877F"/>
      <Setter Property="HorizontalAlignment" Value="Right"/>
      <Setter Property="VerticalAlignment" Value="Center"/>
      <Setter Property="Margin" Value="12,2,0,2"/>
    </Style>
    <Style x:Key="Caption" TargetType="TextBlock">
      <Setter Property="FontSize" Value="10"/>
      <Setter Property="Foreground" Value="#6F6C66"/>
      <Setter Property="HorizontalAlignment" Value="Right"/>
      <Setter Property="Margin" Value="12,0,0,1"/>
    </Style>
  </Window.Resources>

  <Border x:Name="Card" CornerRadius="10" Background="#F21C1B1A" BorderBrush="#2EFFFFFF" BorderThickness="1"
          Padding="13,9,13,9" MinWidth="226">
    <Border.ContextMenu>
      <ContextMenu>
        <MenuItem x:Name="MiRefresh" Header="Refresh now"/>
        <MenuItem x:Name="MiCompact" Header="Compact (double-click)" IsCheckable="True"/>
        <MenuItem x:Name="MiTop" Header="Always on top" IsCheckable="True" IsChecked="True"/>
        <MenuItem x:Name="MiStartup" Header="Start with Windows" IsCheckable="True"/>
        <Separator/>
        <MenuItem x:Name="MiExit" Header="Exit"/>
      </ContextMenu>
    </Border.ContextMenu>
    <!-- Two views share the card; exactly one is visible. Double-click switches between them. -->
    <Grid>
    <StackPanel x:Name="FullView">

      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <Ellipse x:Name="TitleDot" Width="7" Height="7" Fill="#5A5853" VerticalAlignment="Center" Margin="0,1,7,0"/>
        <TextBlock Grid.Column="1" Text="CLAUDE USAGE" FontSize="10" FontWeight="SemiBold" Foreground="#9C9A92"
                   VerticalAlignment="Center" ToolTip="Double-click to shrink"/>
        <TextBlock x:Name="CloseGlyph" Grid.Column="2" Text="&#215;" FontSize="15" Foreground="#6F6C66"
                   Padding="6,0,0,0" Margin="0,-4,-2,-2" Cursor="Hand" ToolTip="Close"/>
      </Grid>

      <Grid Margin="0,5,0,0">
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto" MinWidth="70"/>
        </Grid.ColumnDefinitions>
        <Grid.RowDefinitions>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>

        <TextBlock Grid.Row="0" Grid.Column="1" Text="est. cost" Style="{StaticResource Caption}"/>
        <TextBlock Grid.Row="0" Grid.Column="2" Text="tokens" Style="{StaticResource Caption}"/>

        <TextBlock x:Name="Lbl5h"  Grid.Row="1" Grid.Column="0" Text="Last 5h" Style="{StaticResource Label}"/>
        <TextBlock x:Name="Cost5h" Grid.Row="1" Grid.Column="1" Text="..." Style="{StaticResource Value}"/>
        <TextBlock x:Name="Tok5h"  Grid.Row="1" Grid.Column="2" Text="" Style="{StaticResource Dim}"/>

        <TextBlock x:Name="LblToday"  Grid.Row="2" Grid.Column="0" Text="Today" Style="{StaticResource Label}"/>
        <TextBlock x:Name="CostToday" Grid.Row="2" Grid.Column="1" Text="..." Style="{StaticResource Value}"/>
        <TextBlock x:Name="TokToday"  Grid.Row="2" Grid.Column="2" Text="" Style="{StaticResource Dim}"/>

        <TextBlock x:Name="Lbl7d"  Grid.Row="3" Grid.Column="0" Text="7 days" Style="{StaticResource Label}"/>
        <TextBlock x:Name="Cost7d" Grid.Row="3" Grid.Column="1" Text="..." Style="{StaticResource Value}"/>
        <TextBlock x:Name="Tok7d"  Grid.Row="3" Grid.Column="2" Text="" Style="{StaticResource Dim}"/>
      </Grid>

      <!-- One row per rate-limit window, built in code from whatever the feed reports. -->
      <StackPanel x:Name="LimitsPanel" Visibility="Collapsed" Margin="0,7,0,0">
        <StackPanel x:Name="LimitRows"/>
        <TextBlock x:Name="LimitsAsOf" FontSize="10" Foreground="#6F6C66" Visibility="Collapsed"/>
      </StackPanel>

      <TextBlock x:Name="ModelSplit" FontSize="11" Foreground="#B8B5AD" Margin="0,7,0,0" Text=""/>
      <TextBlock x:Name="Footer" FontSize="10" Foreground="#6F6C66" Margin="0,3,0,0" Text="starting"/>
    </StackPanel>

    <StackPanel x:Name="CompactView" Orientation="Horizontal" Visibility="Collapsed">
      <Ellipse x:Name="CompactDot" Width="7" Height="7" Fill="#5A5853" VerticalAlignment="Center" Margin="0,1,7,0"/>
      <TextBlock x:Name="CompactText" FontFamily="Cascadia Mono, Consolas" FontSize="12" Foreground="#F3F1EA"
                 VerticalAlignment="Center" Text="..."/>
    </StackPanel>
    </Grid>
  </Border>
</Window>
'@

try {
    $script:Window = [System.Windows.Markup.XamlReader]::Parse($xaml)
} catch {
    Write-WidgetLog ("XAML failed to load: {0}" -f $_.Exception.Message)
    throw
}

$script:UI = @{}
foreach ($name in 'Card', 'TitleDot', 'CloseGlyph', 'Lbl5h', 'Cost5h', 'Tok5h', 'LblToday', 'CostToday', 'TokToday',
                  'Lbl7d', 'Cost7d', 'Tok7d', 'LimitsPanel', 'LimitRows', 'LimitsAsOf',
                  'ModelSplit', 'Footer', 'FullView', 'CompactView', 'CompactDot', 'CompactText',
                  'MiRefresh', 'MiCompact', 'MiTop', 'MiStartup', 'MiExit') {
    $script:UI[$name] = $script:Window.FindName($name)
}

$brushes = New-Object System.Windows.Media.BrushConverter
$script:BrushAccent = $brushes.ConvertFromString('#D97757')
$script:BrushHot    = $brushes.ConvertFromString('#E5484D')
$script:BrushIdle   = $brushes.ConvertFromString('#5A5853')
$script:BrushTrack  = $brushes.ConvertFromString('#1FFFFFFF')
$script:BrushText   = $brushes.ConvertFromString('#F3F1EA')
$script:StyleLabel  = $script:Window.FindResource('Label')
$script:StyleDim    = $script:Window.FindResource('Dim')
$script:Dot         = [string][char]0x00B7

$script:Compact     = $false          # compact pill vs full card; toggled by double-click
$script:Positioned  = $false          # true once the window has been placed; gates anchoring and state saves

$script:StartupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude Usage Widget.lnk'
$script:LauncherVbs = Join-Path $PSScriptRoot 'Start-ClaudeUsageWidget.vbs'

function Add-LimitRow($limit) {
    # Label on the left, "NN% left . resets 3d 4h" on the right, and a bar that drains as the
    # allowance is used (full = plenty left), turning red for the last 15%.
    $grid = New-Object System.Windows.Controls.Grid
    $colLabel = New-Object System.Windows.Controls.ColumnDefinition
    $colValue = New-Object System.Windows.Controls.ColumnDefinition
    $colValue.Width = [System.Windows.GridLength]::Auto
    [void]$grid.ColumnDefinitions.Add($colLabel)
    [void]$grid.ColumnDefinitions.Add($colValue)

    $label = New-Object System.Windows.Controls.TextBlock
    $label.Style = $script:StyleLabel
    $label.Text = $limit.Label

    $text = '{0:0}% left' -f $limit.Left
    $tip = '{0:0}% used' -f $limit.Used
    if ($null -ne $limit.Resets) {
        $text = '{0} {1} resets {2}' -f $text, $script:Dot, (Format-Span ($limit.Resets - [datetime]::UtcNow))
        $tip = '{0}, resets {1:ddd d MMM HH:mm}' -f $tip, $limit.Resets.ToLocalTime()
    }
    $value = New-Object System.Windows.Controls.TextBlock
    $value.Style = $script:StyleDim
    $value.Text = $text
    [System.Windows.Controls.Grid]::SetColumn($value, 1)
    [void]$grid.Children.Add($label)
    [void]$grid.Children.Add($value)
    $grid.ToolTip = $tip

    $bar = New-Object System.Windows.Controls.ProgressBar
    $bar.Height = 4
    $bar.Minimum = 0
    $bar.Maximum = 100
    $bar.Value = $limit.Left
    $bar.Background = $script:BrushTrack
    $bar.Foreground = if ($limit.Left -le 15) { $script:BrushHot } else { $script:BrushAccent }
    $bar.BorderThickness = [System.Windows.Thickness]::new(0)
    $bar.Margin = [System.Windows.Thickness]::new(0, 1, 0, 5)
    $bar.ToolTip = $tip

    [void]$script:UI.LimitRows.Children.Add($grid)
    [void]$script:UI.LimitRows.Children.Add($bar)
}

function Update-View {
    $ui = $script:UI
    $s = Get-UsageSummary

    $rows = @(
        @{ Bucket = $s.H5;    Label = $ui.Lbl5h;    Cost = $ui.Cost5h;    Tok = $ui.Tok5h }
        @{ Bucket = $s.Today; Label = $ui.LblToday; Cost = $ui.CostToday; Tok = $ui.TokToday }
        @{ Bucket = $s.D7;    Label = $ui.Lbl7d;    Cost = $ui.Cost7d;    Tok = $ui.Tok7d }
    )
    foreach ($r in $rows) {
        $r.Cost.Text = Format-Cost $r.Bucket.Cost
        $r.Tok.Text  = Format-Tokens $r.Bucket.Tokens
        $tip = Get-BucketTip $r.Bucket
        $r.Label.ToolTip = $tip; $r.Cost.ToolTip = $tip; $r.Tok.ToolTip = $tip
    }

    $split = Get-ModelSplit $s.Today
    $ui.ModelSplit.Text = if ($split) { $split } else { 'no usage yet today' }

    $rl = Read-RateLimits
    $ui.LimitRows.Children.Clear()
    if ($null -eq $rl) {
        $ui.LimitsPanel.Visibility = [System.Windows.Visibility]::Collapsed
    } else {
        $ui.LimitsPanel.Visibility = [System.Windows.Visibility]::Visible
        foreach ($limit in $rl.Windows) { Add-LimitRow $limit }

        # The feed only moves while a Claude Code session is open, so say so once it gets old.
        $readAt = $rl.Updated.ToLocalTime()
        if (([datetime]::UtcNow - $rl.Updated).TotalMinutes -ge $script:FeedStaleMinutes) {
            $format = if ($readAt.Date -eq [datetime]::Today) { 'HH:mm' } else { 'ddd HH:mm' }
            $ui.LimitsAsOf.Text = 'limits as of {0}' -f $readAt.ToString($format)
            $ui.LimitsAsOf.Visibility = [System.Windows.Visibility]::Visible
        } else {
            $ui.LimitsAsOf.Visibility = [System.Windows.Visibility]::Collapsed
        }
    }

    # The dot lights up while Claude has answered something in the last two minutes.
    $live = ([datetime]::UtcNow - $script:LastEventUtc).TotalMinutes -le 2
    $ui.TitleDot.Fill = if ($live) { $script:BrushAccent } else { $script:BrushIdle }

    # Compact pill: the limit remainders when the feed is there, otherwise the cost headline.
    $ui.CompactDot.Fill = $ui.TitleDot.Fill
    $sep = ' {0} ' -f $script:Dot
    $tipLines = @()
    if ($null -ne $rl) {
        $short = @{ five_hour = '5h'; seven_day = 'wk' }
        $shown = @($rl.Windows | Where-Object { $short.ContainsKey($_.Name) })
        if ($shown.Count -eq 0) { $shown = @($rl.Windows | Select-Object -First 2) }
        $bits = foreach ($limit in $shown) {
            $tag = if ($short.ContainsKey($limit.Name)) { $short[$limit.Name] } else { $limit.Label }
            '{0} {1:0}%' -f $tag, $limit.Left
        }
        $ui.CompactText.Text = (@($bits) -join $sep) + ' left'
        $lowest = 100.0
        foreach ($limit in $rl.Windows) {
            if ($limit.Left -lt $lowest) { $lowest = $limit.Left }
            $line = '{0}: {1:0}% left' -f $limit.Label, $limit.Left
            if ($null -ne $limit.Resets) { $line = '{0}, resets {1}' -f $line, (Format-Span ($limit.Resets - [datetime]::UtcNow)) }
            $tipLines += $line
        }
        $ui.CompactText.Foreground = if ($lowest -le 15) { $script:BrushHot } else { $script:BrushText }
    } else {
        $ui.CompactText.Text = '5h {0}{1}today {2}' -f (Format-Cost $s.H5.Cost), $sep, (Format-Cost $s.Today.Cost)
        $ui.CompactText.Foreground = $script:BrushText
    }
    $tipLines += 'Last 5h {0}   Today {1}   7 days {2}' -f (Format-Cost $s.H5.Cost), (Format-Cost $s.Today.Cost), (Format-Cost $s.D7.Cost)
    $tipLines += 'Double-click to expand'
    $ui.CompactView.ToolTip = $tipLines -join "`n"

    if ($script:Queue.Count -gt 0) {
        $ui.Footer.Text = 'scanning {0} of {1} files' -f ($script:ScanTotal - $script:Queue.Count + 1), $script:ScanTotal
    } else {
        $ui.Footer.Text = if ($script:PricingWarning) { $script:PricingWarning } else { 'updated {0:HH:mm:ss}' -f (Get-Date) }
    }
}

function Start-Refresh {
    Find-ChangedFiles
    if ($script:Queue.Count -gt 0) { $script:Pump.Start() }
    Update-View      # time windows slide even when no new data arrived
}

function Save-WidgetState {
    # Right and Bottom are saved as well because the widget changes size (compact mode, limit rows
    # arriving) and keeps its nearest screen edges fixed. Restoring from the matching edge means
    # it reopens exactly where it was, whatever size it starts at.
    if (-not $script:Positioned) { return }
    try {
        $w = $script:Window
        $state = @{
            Left = $w.Left; Top = $w.Top; Right = $w.Left + $w.ActualWidth; Bottom = $w.Top + $w.ActualHeight
            Topmost = $w.Topmost; Compact = $script:Compact
        }
        Set-Content -LiteralPath $script:StatePath -Value ($state | ConvertTo-Json) -Encoding UTF8
    } catch { }
}

function Set-CompactMode([bool]$On) {
    $script:Compact = $On
    $ui = $script:UI
    if ($On) {
        $ui.FullView.Visibility = [System.Windows.Visibility]::Collapsed
        $ui.CompactView.Visibility = [System.Windows.Visibility]::Visible
        $ui.Card.MinWidth = 0
        $ui.Card.Padding = [System.Windows.Thickness]::new(10, 5, 11, 6)
    } else {
        $ui.CompactView.Visibility = [System.Windows.Visibility]::Collapsed
        $ui.FullView.Visibility = [System.Windows.Visibility]::Visible
        $ui.Card.MinWidth = 226
        $ui.Card.Padding = [System.Windows.Thickness]::new(13, 9, 13, 9)
    }
    $ui.MiCompact.IsChecked = $On
}

function Set-StartupShortcut([bool]$Enable) {
    if ($Enable) {
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut($script:StartupLink)
        $link.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
        $link.Arguments = '"{0}"' -f $script:LauncherVbs
        $link.WorkingDirectory = $PSScriptRoot
        $link.Description = 'Claude usage widget'
        $link.Save()
    } elseif (Test-Path -LiteralPath $script:StartupLink) {
        Remove-Item -LiteralPath $script:StartupLink -Force
    }
}

# The pump drains the scan queue in short slices so the window stays responsive
# while a big backlog (first launch) is being read.
$script:Pump = New-Object System.Windows.Threading.DispatcherTimer
$script:Pump.Interval = [TimeSpan]::FromMilliseconds(15)
$script:LastPartialPaint = [datetime]::MinValue
$script:Pump.Add_Tick({
    try {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $more = $true
        while ($more -and $sw.ElapsedMilliseconds -lt 100) { $more = Invoke-ScanStep }
        if (-not $more) {
            $script:Pump.Stop()
            Update-View
        } elseif (([datetime]::UtcNow - $script:LastPartialPaint).TotalMilliseconds -ge 750) {
            $script:LastPartialPaint = [datetime]::UtcNow
            Update-View
        }
    } catch {
        $script:Pump.Stop()
        Write-WidgetLog ("pump: {0}" -f $_.Exception.Message)
        $script:UI.Footer.Text = 'scan error, see widget.log'
    }
})

# Windows keeps the taskbar in the same always-on-top band as this window, and the taskbar puts
# itself back on top whenever it is used. A pill parked on the taskbar would silently disappear
# behind it. So while the widget sticks out of the work area, keep re-asserting its place.
# Skipped while the pointer is over the widget (it is visible then, and its tooltip and menu
# must stay above it).
$script:TopmostTimer = New-Object System.Windows.Threading.DispatcherTimer
$script:TopmostTimer.Interval = [TimeSpan]::FromSeconds(1)
$script:TopmostTimer.Add_Tick({
    try {
        $w = $script:Window
        if (-not $script:Positioned -or -not $script:UI.MiTop.IsChecked) { return }
        if ($w.IsMouseOver -or $script:UI.Card.ContextMenu.IsOpen -or $null -ne $script:DragOrigin) { return }
        $area = [System.Windows.SystemParameters]::WorkArea
        $outside = ($w.Top -lt $area.Top) -or ($w.Left -lt $area.Left) -or
                   (($w.Top + $w.ActualHeight) -gt $area.Bottom) -or (($w.Left + $w.ActualWidth) -gt $area.Right)
        if (-not $outside) { return }
        $w.Topmost = $false      # off then on moves the window to the front of the always-on-top band
        $w.Topmost = $true
    } catch { }
})

$script:RefreshTimer = New-Object System.Windows.Threading.DispatcherTimer
$script:RefreshTimer.Interval = [TimeSpan]::FromSeconds($script:RefreshSeconds)
$script:RefreshTimer.Add_Tick({
    try { Start-Refresh }
    catch {
        Write-WidgetLog ("refresh: {0}" -f $_.Exception.Message)
        $script:UI.Footer.Text = 'refresh error, see widget.log'
    }
})

# Click handling: a double-click toggles compact mode, a press-and-move drags the window.
# DragMove runs its own modal loop, so it only starts once the pointer has really moved with the
# button held. Calling it on every mouse-down made double-clicks unreliable while the UI was busy.
$script:DragOrigin = $null
$script:Window.Add_MouseLeftButtonDown({
    param($s, $e)
    try {
        if ($e.ClickCount -ge 2) {
            $script:DragOrigin = $null
            Set-CompactMode (-not $script:Compact)   # the resize lands in SizeChanged, which re-anchors and saves
            return
        }
        $script:DragOrigin = $e.GetPosition($script:Window)
        [void]$script:Window.CaptureMouse()          # keep receiving moves even if the pointer leaves the tiny pill
    } catch { }
})
$script:Window.Add_MouseMove({
    param($s, $e)
    try {
        if ($null -eq $script:DragOrigin) { return }
        if ($e.LeftButton -ne [System.Windows.Input.MouseButtonState]::Pressed) {
            $script:DragOrigin = $null
            $script:Window.ReleaseMouseCapture()
            return
        }
        $p = $e.GetPosition($script:Window)
        if ([Math]::Abs($p.X - $script:DragOrigin.X) -ge 3 -or [Math]::Abs($p.Y - $script:DragOrigin.Y) -ge 3) {
            $script:DragOrigin = $null
            $script:Window.ReleaseMouseCapture()
            $script:Window.DragMove()                # returns when the mouse button is released
            Save-WidgetState
        }
    } catch { }
})
$script:Window.Add_MouseLeftButtonUp({
    param($s, $e)
    $script:DragOrigin = $null
    try { $script:Window.ReleaseMouseCapture() } catch { }
})

# The window sizes itself to its content, and WPF grows or shrinks it from the top-left corner.
# For a widget parked near the right or bottom of the screen that looks like drifting, so keep
# whichever edges are nearest the screen edge fixed instead.
$script:Window.Add_SizeChanged({
    param($s, $e)
    try {
        if (-not $script:Positioned) { return }
        $prev = $e.PreviousSize
        $new = $e.NewSize
        if ($prev.Width -le 0 -or $prev.Height -le 0) { return }
        $w = $script:Window
        $area = [System.Windows.SystemParameters]::WorkArea
        $left = $w.Left
        $top  = $w.Top
        if (($left + $prev.Width / 2) -gt ($area.Left + $area.Width / 2))  { $left += ($prev.Width - $new.Width) }
        if (($top + $prev.Height / 2) -gt ($area.Top + $area.Height / 2))  { $top  += ($prev.Height - $new.Height) }

        # A card dragged partly past the screen edge would otherwise shrink to a pill that is
        # entirely off-screen (its fixed edge was the one outside). Always end up fully visible.
        # The whole screen, not the work area, so the pill may still sit on the taskbar.
        $vsLeft = [System.Windows.SystemParameters]::VirtualScreenLeft
        $vsTop  = [System.Windows.SystemParameters]::VirtualScreenTop
        $maxLeft = $vsLeft + [System.Windows.SystemParameters]::VirtualScreenWidth - $new.Width
        $maxTop  = $vsTop + [System.Windows.SystemParameters]::VirtualScreenHeight - $new.Height
        $left = [Math]::Max($vsLeft, [Math]::Min($left, $maxLeft))
        $top  = [Math]::Max($vsTop, [Math]::Min($top, $maxTop))

        if ($left -ne $w.Left) { $w.Left = $left }
        if ($top -ne $w.Top)   { $w.Top = $top }
        Save-WidgetState
    } catch { }
})

$script:UI.CloseGlyph.Add_MouseLeftButtonDown({
    param($s, $e)
    $e.Handled = $true                 # keep the window-level drag handler out of it
    $script:Window.Close()
})

# The Startup shortcut can be added or removed outside the widget, so re-read it whenever the menu opens.
$script:UI.Card.ContextMenu.Add_Opened({
    $script:UI.MiStartup.IsChecked = Test-Path -LiteralPath $script:StartupLink
    $script:UI.MiCompact.IsChecked = $script:Compact
})
$script:UI.MiCompact.Add_Click({ Set-CompactMode ([bool]$script:UI.MiCompact.IsChecked) })

$script:UI.MiRefresh.Add_Click({ try { Start-Refresh } catch { Write-WidgetLog ("manual refresh: {0}" -f $_.Exception.Message) } })
$script:UI.MiExit.Add_Click({ $script:Window.Close() })
$script:UI.MiTop.Add_Click({
    $script:Window.Topmost = [bool]$script:UI.MiTop.IsChecked
    Save-WidgetState
})
$script:UI.MiStartup.Add_Click({
    try { Set-StartupShortcut ([bool]$script:UI.MiStartup.IsChecked) }
    catch {
        Write-WidgetLog ("startup shortcut: {0}" -f $_.Exception.Message)
        $script:UI.MiStartup.IsChecked = Test-Path -LiteralPath $script:StartupLink
    }
})

# Read the saved state before the window shows, so a widget that was left compact starts compact
# and its first layout already has the right size.
$script:SavedState = $null
if (Test-Path -LiteralPath $script:StatePath) {
    try { $script:SavedState = ConvertFrom-Json -InputObject (Get-Content -LiteralPath $script:StatePath -Raw) } catch { }
}
if ($null -ne $script:SavedState -and $script:SavedState.Compact -eq $true) { Set-CompactMode $true }

$script:Window.Add_Loaded({
    try {
        $w = $script:Window
        $area = [System.Windows.SystemParameters]::WorkArea
        $left = $area.Right - $w.ActualWidth - 16
        $top  = $area.Bottom - $w.ActualHeight - 16

        $state = $script:SavedState
        if ($null -ne $state -and $null -ne $state.Left -and $null -ne $state.Top) {
            try {
                $savedLeft = [double]$state.Left
                $savedTop  = [double]$state.Top
                $savedRight  = if ($null -ne $state.Right)  { [double]$state.Right }  else { $savedLeft + $w.ActualWidth }
                $savedBottom = if ($null -ne $state.Bottom) { [double]$state.Bottom } else { $savedTop + $w.ActualHeight }

                # Same rule as SizeChanged: a widget on the right or bottom half hangs off that edge.
                $candLeft = $savedLeft
                $candTop  = $savedTop
                if ((($savedLeft + $savedRight) / 2) -gt ($area.Left + $area.Width / 2))  { $candLeft = $savedRight - $w.ActualWidth }
                if ((($savedTop + $savedBottom) / 2) -gt ($area.Top + $area.Height / 2)) { $candTop  = $savedBottom - $w.ActualHeight }

                $vsLeft = [System.Windows.SystemParameters]::VirtualScreenLeft
                $vsTop  = [System.Windows.SystemParameters]::VirtualScreenTop
                $vsRight  = $vsLeft + [System.Windows.SystemParameters]::VirtualScreenWidth
                $vsBottom = $vsTop + [System.Windows.SystemParameters]::VirtualScreenHeight
                # Only restore a position that is still on a connected screen.
                $onScreen = ($candLeft -ge $vsLeft) -and ($candTop -ge $vsTop) -and
                            (($candLeft + 60) -le $vsRight) -and (($candTop + 20) -le $vsBottom)
                if ($onScreen) { $left = $candLeft; $top = $candTop }
                if ($null -ne $state.Topmost) {
                    $w.Topmost = [bool]$state.Topmost
                    $script:UI.MiTop.IsChecked = [bool]$state.Topmost
                }
            } catch { }
        }
        $w.Left = $left
        $w.Top  = $top
        $script:Positioned = $true      # from here on, size changes re-anchor and state gets saved

        $script:UI.MiStartup.IsChecked = Test-Path -LiteralPath $script:StartupLink
        $script:RefreshTimer.Start()
        $script:TopmostTimer.Start()
        Start-Refresh
    } catch {
        Write-WidgetLog ("loaded: {0}" -f $_.Exception.Message)
        $script:UI.Footer.Text = 'startup error, see widget.log'
    }
})

$script:Window.Add_Closing({
    $script:Pump.Stop()
    $script:RefreshTimer.Stop()
    $script:TopmostTimer.Stop()
    Save-WidgetState
})

try {
    [void]$script:Window.ShowDialog()
} catch {
    Write-WidgetLog ("fatal: {0}" -f $_.Exception.Message)
    throw
} finally {
    try { $script:Mutex.ReleaseMutex() } catch { }
    $script:Mutex.Dispose()
}
