<#
.SYNOPSIS
    Stop the ULOO development services started by scripts/dev-up.ps1.

.DESCRIPTION
    Candidates are gathered from two places:

      * the pid files dev-up.ps1 writes into tmp\dev-pids
      * whoever is listening on 8200 / 5001 / 3000

    The second source matters because dev-up.ps1 reuses a service that is
    already running and only then records its pid. Before that was recorded, a
    server started by an earlier session could not be stopped by this script at
    all.

    A candidate is only terminated when it is recognisably part of this
    checkout: the command line, or that of an ancestor, must point into the
    repository, and the executable must be an interpreter this repository
    starts (node or python). Anything else holding one of those ports - a
    database, an unrelated dev server, another project's process - is reported
    and left alone. Pass -Force to kill such a process anyway.

    PostgreSQL, Redis and the plugin daemon are containers and are left running.
    Docker Desktop is not touched.

.EXAMPLE
    pwsh -File scripts/dev-down.ps1

.EXAMPLE
    pwsh -File scripts/dev-down.ps1 -Force
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PidDir = Join-Path $RepoRoot 'tmp\dev-pids'

# Port -> service name, mirroring dev-up.ps1. String keys for the same reason
# the candidate table uses them: an integer key on an ordered dictionary is
# interpreted as a positional index.
$servicePorts = @{
    '8200' = 'ULOO Core'
    '5001' = 'Dify API'
    '3000' = 'Dify Web'
}

function Get-PortOwner {
    param([int]$Port)
    $lines = & netstat.exe -ano -p TCP 2>$null
    foreach ($line in $lines) {
        if ($line -match "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") {
            return [int]$Matches[1]
        }
    }
    return $null
}

# Next.js dev spawns workers that inherit nothing useful in their own command
# line, so walk up a few levels looking for a frame that points at this repo.
function Test-BelongsToRepo {
    param(
        [int]$ProcessId,
        [int]$MaxDepth = 4
    )
    $current = $ProcessId
    for ($depth = 0; $depth -lt $MaxDepth -and $current -gt 0; $depth++) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$current" -ErrorAction SilentlyContinue
        if (-not $proc) { break }
        if ($proc.CommandLine -and $proc.CommandLine -like "*$RepoRoot*") { return $true }
        if ($proc.ExecutablePath -and $proc.ExecutablePath -like "*$RepoRoot*") { return $true }
        $current = [int]$proc.ParentProcessId
    }
    return $false
}

# Collect candidates: pid files first, then anything still holding a port.
#
# A plain Hashtable keyed by string, not [ordered]@{}. OrderedDictionary exposes
# both this[object key] and this[int index], and PowerShell binds the integer
# form, so $candidates[30084] is read as "item number 30084" and throws
# ArgumentOutOfRangeException instead of storing a key.
$candidates = @{}

if (Test-Path $PidDir) {
    foreach ($pidFile in (Get-ChildItem -Path $PidDir -Filter '*.pid' -ErrorAction SilentlyContinue)) {
        $raw = (Get-Content $pidFile.FullName -ErrorAction SilentlyContinue | Select-Object -First 1)
        $recordedPid = 0
        if ([int]::TryParse($raw, [ref]$recordedPid) -and $recordedPid -gt 0) {
            $candidates["$recordedPid"] = "recorded by dev-up ($($pidFile.BaseName))"
        }
        Remove-Item $pidFile.FullName -Force
    }
}

foreach ($portKey in $servicePorts.Keys) {
    $port = [int]$portKey
    $ownerPid = Get-PortOwner -Port $port
    if ($ownerPid -and -not $candidates.ContainsKey("$ownerPid")) {
        $candidates["$ownerPid"] = "$($servicePorts[$portKey]) holding port $port"
    }
}

# Killing a parent takes its descendants with it, so a candidate whose ancestor
# is already in the set would only be reported as "gone" a moment later. Drop
# those up front to keep the output honest.
function Test-AncestorInSet {
    param(
        [int]$ProcessId,
        [hashtable]$Set,
        [int]$MaxDepth = 4
    )
    $current = $ProcessId
    for ($depth = 0; $depth -lt $MaxDepth -and $current -gt 0; $depth++) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$current" -ErrorAction SilentlyContinue
        if (-not $proc) { break }
        $parentId = [int]$proc.ParentProcessId
        if ($parentId -le 0) { break }
        if ($Set.ContainsKey("$parentId")) { return $true }
        $current = $parentId
    }
    return $false
}

foreach ($candidateKey in @($candidates.Keys)) {
    if (Test-AncestorInSet -ProcessId ([int]$candidateKey) -Set $candidates) {
        $candidates.Remove($candidateKey)
    }
}

if ($candidates.Count -eq 0) {
    Write-Host 'Nothing to stop: no recorded pids and ports 8200/5001/3000 are free.'
    exit 0
}

$stopped = 0
$kept = @()

foreach ($candidateKey in $candidates.Keys) {
    $candidatePid = [int]$candidateKey
    $reason = $candidates[$candidateKey]

    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$candidatePid" -ErrorAction SilentlyContinue
    # A process that is mid-exit reports no command line. Treat that as gone
    # rather than as a stranger this script must not touch.
    if (-not $proc -or [string]::IsNullOrWhiteSpace($proc.CommandLine)) {
        Write-Host ("[gone]   pid {0} is no longer running ({1})" -f $candidatePid, $reason)
        continue
    }

    $exeLeaf = ''
    if ($proc.ExecutablePath) {
        $exeLeaf = [System.IO.Path]::GetFileName($proc.ExecutablePath).ToLowerInvariant()
    }
    $interpreterIsOurs = $exeLeaf -in @('node.exe', 'python.exe', 'pythonw.exe')
    $pathIsOurs = Test-BelongsToRepo -ProcessId $candidatePid

    if (-not $Force -and -not ($interpreterIsOurs -and $pathIsOurs)) {
        Write-Host ("[keep]   pid {0} ({1}, {2}) - {3}" -f $candidatePid, $proc.Name, $reason, 'not identified as this repository; use -Force to stop it anyway')
        $kept += $candidatePid
        continue
    }

    # Next.js and Flask both spawn children; stop the tree so no orphan keeps
    # holding the port.
    & taskkill.exe /PID $candidatePid /T /F | Out-Null
    Write-Host ("[stop]   pid {0} ({1}, {2})" -f $candidatePid, $proc.Name, $reason)
    $stopped++
}

Write-Host ''
Write-Host ("Stopped {0} process tree(s)." -f $stopped)
if ($kept.Count -gt 0) {
    Write-Host ("Left running (not ours): {0}" -f ($kept -join ', '))
}
Write-Host 'PostgreSQL, Redis, the plugin daemon and Docker Desktop were left alone.'
exit 0
