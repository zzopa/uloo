<#
.SYNOPSIS
    Start the ULOO development stack from source and wait until it is usable.

.DESCRIPTION
    Launches the three source-built processes the product needs, then blocks
    until each one answers its health probe:

        ULOO Core   http://localhost:8200   services/uloo-core   (uvicorn)
        Dify API    http://localhost:5001   vendor/dify/api      (python app.py)
        Dify Web    http://localhost:3000   vendor/dify/web      (next dev)

    PostgreSQL, Redis and the Dify plugin daemon are infrastructure and are NOT
    started here; see deploy/compose.dev.yaml.

    Services already listening on their port are left alone, so this script is
    safe to re-run.

    IMPORTANT - do not start Dify API from an MSYS / Git-Bash shell.
    The Cygwin runtime aborts the interpreter with

        fatal error - Internal error: TP_NUM_C_BUFS too small: 50

    before Flask is ever imported. That is a launcher incompatibility, not a
    Dify bug and not an OpenDAL problem: `import opendal` succeeds on this
    machine. Use this script, or run the command from PowerShell / cmd.

.EXAMPLE
    pwsh -File scripts/dev-up.ps1
    pwsh -File scripts/dev-up.ps1 -SkipWeb
#>
[CmdletBinding()]
param(
    [switch]$SkipCore,
    [switch]$SkipDifyApi,
    [switch]$SkipWeb,
    [int]$TimeoutSeconds = 300,
    # V8 old-space ceiling for the Next.js dev server. Node's own default on
    # this machine is 4288 MB, so 4096 holds the JS heap at that level instead
    # of raising it. The runaway growth happens outside the heap; see below.
    [int]$WebMaxOldSpaceMB = 4096,
    # Ceiling for Turbopack's own allocator, in MB, handed to next.config.ts as
    # ULOO_TURBOPACK_MEMORY_LIMIT_MB. This bounds the main process only; see
    # -WebPluginRuntime for the helpers, which are what actually ran away.
    [int]$WebTurbopackMemoryLimitMB = 6144,
    # How Turbopack hosts the compilation helpers that need a Node runtime.
    # 'childProcesses' is the upstream default: one helper process per concurrent
    # task, which is where the memory goes (a full Dify compile measured 42 of
    # them at ~500 MB each). 'workerThreads' would share one process and measured
    # only 2 processes / 5 GB - but it is broken in Next 16.2.12, dying with
    # ERR_SOCKET_BAD_PORT in turbopack-node/child_process createIpc. Kept as an
    # option so it can be re-enabled once upstream fixes it.
    [ValidateSet('childProcesses', 'workerThreads')]
    [string]$WebPluginRuntime = 'childProcesses',
    # code-inspector-plugin is what makes Turbopack start those helpers, and it
    # only powers "click an element to jump to source". Off by default.
    [ValidateSet('off', 'on')]
    [string]$WebCodeInspector = 'off',
    # Which bundler `next dev` uses. Turbopack's Node-runtime helper pool is what
    # exhausts memory on this machine and it cannot be bounded: PostCSS and MDX
    # need a Node runtime too, so removing one loader (code-inspector) measured
    # only 42 -> 23 helpers and 21 GB -> 18 GB, still enough to stall the system.
    # The only knob that tames it, turbopackPluginRuntimeStrategy=workerThreads,
    # is broken in Next 16.2.12. webpack has no such pool and honours
    # --max-old-space-size, at the cost of slower compiles.
    [ValidateSet('webpack', 'turbopack')]
    [string]$WebBundler = 'webpack',
    # Seconds to wait for an already-starting Docker daemon before giving up.
    [int]$DockerWaitSeconds = 60
)

$ErrorActionPreference = 'Stop'
# Invoke-WebRequest draws a progress bar that floods the console for the
# multi-megabyte Next.js dev payload. Only the health result matters here.
$ProgressPreference = 'SilentlyContinue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot 'tmp\dev-logs'
$PidDir = Join-Path $RepoRoot 'tmp\dev-pids'
New-Item -ItemType Directory -Force -Path $LogDir, $PidDir | Out-Null

function Test-PortOpen {
    param([int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(400)) { return $false }
        $client.EndConnect($async)
        return $true
    }
    catch { return $false }
    finally { $client.Close() }
}

function Resolve-Command {
    param([string]$Name)
    $found = Get-Command $Name -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    return $null
}

# Which process owns a listening port. dev-down needs this: a service that was
# already running (started by an earlier session, or by hand) is reused here and
# never gets a pid recorded otherwise, which leaves it unstoppable later.
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

# Next.js dev (Turbopack) has no heap ceiling of its own and will happily grow
# until Windows runs out of commit charge: on 2026-09-21 a single node.exe from
# `next dev` reached 26183368704 bytes and took the whole session down with it
# (System log, event 2004). Cap the V8 old space per service instead.
#
# The limit is appended rather than assigned, because the host environment may
# already carry NODE_OPTIONS (this workspace runs Node through a shim that needs
# --require=<path>). Overwriting it would silently break that shim, so the shim
# flag is preserved and any previous cap is replaced instead of stacked.
function Merge-NodeOptions {
    param(
        [string]$Base,
        [int]$MaxOldSpaceMB
    )
    $limit = "--max-old-space-size=$MaxOldSpaceMB"
    if ([string]::IsNullOrWhiteSpace($Base)) { return $limit }
    $stripped = ([regex]::Replace($Base, '--max-old-space-size=\d+', '')).Trim()
    if ([string]::IsNullOrWhiteSpace($stripped)) { return $limit }
    return "$stripped $limit"
}

# PostgreSQL, Redis and the plugin daemon all live in Docker. When the daemon is
# down, ULOO Core and the Dify API start successfully and then fail every query,
# which looks like an application bug. Probe it up front and say so plainly.
function Test-DockerDaemon {
    $docker = Resolve-Command 'docker'
    if (-not $docker) { return $false }
    & $docker info --format '{{.ServerVersion}}' *> $null
    return ($LASTEXITCODE -eq 0)
}

function Wait-Health {
    param(
        [string]$Url,
        [int]$TimeoutSeconds
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) { return $true }
        }
        catch {
            # Not up yet, or answering with an error while it finishes booting.
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

# ULOO Core runs from its own virtualenv; the Dify services run from theirs.
$corePython = Join-Path $RepoRoot 'services\uloo-core\.venv\Scripts\python.exe'
$difyPython = Join-Path $RepoRoot 'vendor\dify\api\.venv\Scripts\python.exe'

# Next is invoked through node rather than `npm run dev`. Start-Process refuses
# to run a .cmd shim once its output is redirected ("%1 is not a valid Win32
# application"), and going straight to the CLI keeps the process tree shallow
# enough that dev-down can stop it by id. The dev script is a bare `next dev`
# with no pre-step, so nothing is lost by skipping the package manager.
$nodeExe = Resolve-Command 'node'
$nextBin = Join-Path $RepoRoot 'vendor\dify\web\node_modules\next\dist\bin\next'

$webDevArgs = @($nextBin, 'dev')
if ($WebBundler -eq 'webpack') { $webDevArgs += '--webpack' }

$services = @(
    [pscustomobject]@{
        Name    = 'ULOO Core'
        Port    = 8200
        Enabled = -not $SkipCore
        Skip    = 'skipped by -SkipCore'
        Exe     = $corePython
        Args    = @('-m', 'uvicorn', 'uloo.main:app', '--host', '127.0.0.1', '--port', '8200')
        WorkDir = Join-Path $RepoRoot 'services\uloo-core'
        Health  = 'http://127.0.0.1:8200/api/v1/health/live'
        Log     = 'uloo-core.log'
    }
    [pscustomobject]@{
        Name    = 'Dify API'
        Port    = 5001
        Enabled = -not $SkipDifyApi
        Skip    = 'skipped by -SkipDifyApi'
        Exe     = $difyPython
        Args    = @('app.py')
        WorkDir = Join-Path $RepoRoot 'vendor\dify\api'
        Health  = 'http://127.0.0.1:5001/health'
        Log     = 'dify-api.log'
        # Do not inherit a host-wide LOG_FORMAT value such as "json". Dify
        # treats LOG_FORMAT as a Python logging pattern, while the structured
        # output selector is LOG_OUTPUT_FORMAT. A leaked value makes the API
        # crash before binding port 5001.
        Env     = @{
            LOG_OUTPUT_FORMAT = 'text'
            LOG_FORMAT = '%(asctime)s.%(msecs)03d %(levelname)s [%(threadName)s] [%(filename)s:%(lineno)d] %(trace_id)s - %(message)s'
        }
    }
    [pscustomobject]@{
        Name    = 'Dify Web'
        Port    = 3000
        Enabled = -not $SkipWeb
        Skip    = 'skipped by -SkipWeb'
        Exe     = $nodeExe
        Args    = $webDevArgs
        WorkDir = Join-Path $RepoRoot 'vendor\dify\web'
        Health  = 'http://127.0.0.1:3000/'
        Log     = 'dify-web.log'
        NodeMaxOldSpaceMB = $WebMaxOldSpaceMB
        Env     = @{
            ULOO_TURBOPACK_MEMORY_LIMIT_MB = "$WebTurbopackMemoryLimitMB"
            ULOO_TURBOPACK_PLUGIN_RUNTIME  = $WebPluginRuntime
            ULOO_CODE_INSPECTOR            = $WebCodeInspector
        }
    }
)

$started = @()
$reused = @()

# ULOO Core and the Dify API read PostgreSQL and Redis on every request, and
# both of those are Docker containers here. Failing fast beats starting two
# services that will only answer 500s.
$needsDocker = @($services | Where-Object {
        $_.Enabled -and
        $_.Name -in @('ULOO Core', 'Dify API') -and
        -not (Test-PortOpen -Port $_.Port)
    }).Count -gt 0

# A missing Next CLI means the web dependencies were never installed; say that
# rather than dying later with a bare "file not found" from Start-Process.
if ((-not $SkipWeb) -and (-not (Test-Path $nextBin))) {
    throw "Cannot start Dify Web: Next CLI not found at $nextBin. Install vendor\dify\web dependencies first."
}

if ($needsDocker) {
    Write-Host 'Probing infrastructure (Docker)...'
    $dockerDeadline = (Get-Date).AddSeconds($DockerWaitSeconds)
    $dockerReady = Test-DockerDaemon
    while (-not $dockerReady -and (Get-Date) -lt $dockerDeadline) {
        Start-Sleep -Seconds 3
        $dockerReady = Test-DockerDaemon
    }

    if (-not $dockerReady) {
        Write-Host ''
        Write-Host '[FAIL]   Docker daemon is not reachable.'
        Write-Host '         PostgreSQL, Redis and the plugin daemon run as containers here, so'
        Write-Host '         ULOO Core and the Dify API cannot serve requests without it.'
        Write-Host ''
        Write-Host '         Start Docker Desktop and wait until it reports "Engine running",'
        Write-Host '         then re-run this script. Confirm with:  docker info'
        exit 1
    }
    Write-Host '[ready]  Docker daemon'
}

foreach ($service in $services) {
    if (-not $service.Enabled) {
        Write-Host ("[skip]   {0,-10} {1}" -f $service.Name, $service.Skip)
        continue
    }

    if (Test-PortOpen -Port $service.Port) {
        # Record the current owner so dev-down can still stop it later, even
        # though this run did not start it.
        $holderPid = Get-PortOwner -Port $service.Port
        if ($holderPid) {
            Set-Content -Path (Join-Path $PidDir ("$($service.Log).pid")) -Value $holderPid -Encoding ASCII
            Write-Host ("[reuse]  {0,-10} already listening on port {1} (pid {2})" -f $service.Name, $service.Port, $holderPid)
        }
        else {
            Write-Host ("[reuse]  {0,-10} already listening on port {1}" -f $service.Name, $service.Port)
        }
        $reused += $service
        continue
    }

    if (-not $service.Exe -or -not (Test-Path $service.Exe)) {
        throw "Cannot start $($service.Name): executable not found at $($service.Exe)"
    }
    if (-not (Test-Path $service.WorkDir)) {
        throw "Cannot start $($service.Name): working directory not found at $($service.WorkDir)"
    }

    $outLog = Join-Path $LogDir $service.Log
    $errLog = Join-Path $LogDir ($service.Log -replace '\.log$', '.err.log')

    # Start-Process in Windows PowerShell 5.1 has no -Environment, so the cap and
    # any per-service overrides are set on this process for the duration of the
    # launch and put back afterwards.
    $savedNodeOptions = $env:NODE_OPTIONS
    if ($service.NodeMaxOldSpaceMB) {
        $env:NODE_OPTIONS = Merge-NodeOptions -Base $savedNodeOptions -MaxOldSpaceMB $service.NodeMaxOldSpaceMB
        Write-Host ("[limit]  {0,-10} NODE_OPTIONS={1}" -f $service.Name, $env:NODE_OPTIONS)
    }

    $restoreEnv = @{ NODE_OPTIONS = $savedNodeOptions }
    if ($service.Exe -in @($corePython, $difyPython)) {
        $venvConfig = Join-Path $service.WorkDir '.venv\pyvenv.cfg'
        $pythonHomeLine = Get-Content $venvConfig | Where-Object { $_ -match '^home = ' } | Select-Object -First 1
        $restoreEnv['PYTHONHOME'] = $env:PYTHONHOME
        $env:PYTHONHOME = $pythonHomeLine -replace '^home = ', ''
    }
    if ($service.Env) {
        foreach ($key in $service.Env.Keys) {
            $restoreEnv[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
            [Environment]::SetEnvironmentVariable($key, $service.Env[$key], 'Process')
            Write-Host ("[limit]  {0,-10} {1}={2}" -f $service.Name, $key, $service.Env[$key])
        }
    }

    try {
        $process = Start-Process -FilePath $service.Exe `
            -ArgumentList $service.Args `
            -WorkingDirectory $service.WorkDir `
            -RedirectStandardOutput $outLog `
            -RedirectStandardError $errLog `
            -WindowStyle Hidden -PassThru
    }
    finally {
        foreach ($key in $restoreEnv.Keys) {
            [Environment]::SetEnvironmentVariable($key, $restoreEnv[$key], 'Process')
        }
    }

    Set-Content -Path (Join-Path $PidDir ("$($service.Log).pid")) -Value $process.Id -Encoding ASCII
    $service | Add-Member -NotePropertyName ProcessId -NotePropertyValue $process.Id -Force
    $service | Add-Member -NotePropertyName OutLog -NotePropertyValue $outLog -Force
    $service | Add-Member -NotePropertyName ErrLog -NotePropertyValue $errLog -Force

    Write-Host ("[start]  {0,-10} pid {1}  -> {2}" -f $service.Name, $process.Id, $outLog)
    $started += $service
}

Write-Host ''
Write-Host 'Waiting for health probes (first Next.js compile can take a while)...'

$failed = @()
foreach ($service in $services) {
    if (-not $service.Enabled) { continue }

    if (Wait-Health -Url $service.Health -TimeoutSeconds $TimeoutSeconds) {
        Write-Host ("[ready]  {0,-10} {1}" -f $service.Name, $service.Health)
    }
    else {
        Write-Host ("[FAIL]   {0,-10} {1} did not answer within {2}s" -f $service.Name, $service.Health, $TimeoutSeconds)
        if ($service.ErrLog -and (Test-Path $service.ErrLog)) {
            Write-Host "         last stderr lines:"
            Get-Content $service.ErrLog -Tail 15 | ForEach-Object { Write-Host "         $_" }
        }
        $failed += $service.Name
    }
}

Write-Host ''
if ($failed.Count -gt 0) {
    Write-Host ("Not ready: {0}" -f ($failed -join ', '))
    Write-Host 'Logs are under tmp\dev-logs\. Stop everything with scripts\dev-down.ps1.'
    exit 1
}

Write-Host 'ULOO development stack is up.'
Write-Host ''
Write-Host '  Dify Web   http://localhost:3000   <- the only UI you need'
Write-Host '  Dify API   http://localhost:5001   GET /console/api/setup'
Write-Host '  ULOO Core  http://localhost:8200   GET /api/v1/health/live'
Write-Host ''
Write-Host 'Logs: tmp\dev-logs\   Stop: scripts\dev-down.ps1'
exit 0
