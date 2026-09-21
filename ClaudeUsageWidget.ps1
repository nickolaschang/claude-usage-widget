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

.PARAMETER ProjectsRoot
    Override the transcripts folder. Defaults to $env:CLAUDE_CONFIG_DIR\projects or
    ~\.claude\projects.
#>
[CmdletBinding()]
param(
    [switch]$SelfTest,
    [string]$ProjectsRoot
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

# USD per million tokens (input / output), list prices checked 21 Sep 2026 against
# https://platform.claude.com/docs/en/about-claude/pricing
# First substring match on the model id wins, so keep specific entries above general ones.
# The empty Match at the end is the fallback. ReadMult is the cache-read price as a multiple
# of the input price: Fable and Mythos 5.1 bill cache reads at 0.025x, everything else at 0.1x.
$script:Pricing = @(
    [pscustomobject]@{ Match = 'fable';    Label = 'Fable';  In = 10.00; Out = 50.00; ReadMult = 0.025 }
    [pscustomobject]@{ Match = 'mythos';   Label = 'Mythos'; In = 10.00; Out = 50.00; ReadMult = 0.025 }  # assumed same as Fable
    [pscustomobject]@{ Match = 'opus';     Label = 'Opus';   In = 5.00;  Out = 25.00; ReadMult = 0.10 }   # Opus 5, Opus 4.8
    [pscustomobject]@{ Match = 'sonnet-4'; Label = 'Sonnet'; In = 3.00;  Out = 15.00; ReadMult = 0.10 }   # older Sonnet 4.x
    [pscustomobject]@{ Match = 'sonnet';   Label = 'Sonnet'; In = 2.00;  Out = 10.00; ReadMult = 0.10 }   # Sonnet 5
    [pscustomobject]@{ Match = 'haiku';    Label = 'Haiku';  In = 1.00;  Out = 5.00;  ReadMult = 0.10 }   # Haiku 4.5
    [pscustomobject]@{ Match = '';         Label = 'Other';  In = 3.00;  Out = 15.00; ReadMult = 0.10 }
)

# Cache writes are priced as a multiple of the model's input price (same for every model).
$script:CacheWrite5mMult = 1.25
$script:CacheWrite1hMult = 2.00

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

