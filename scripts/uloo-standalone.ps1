# AI生成
# scripts/uloo-standalone.ps1
# ULOO Standalone Platform Script

param (
    [Parameter(Position=0)]
    [ValidateSet("up", "down", "status", "restart")]
    [string]$Action = "up",
    [switch]$Docker = $false
)

$ErrorActionPreference = "Stop"

function Start-DockerMode {
    Write-Host "Starting ULOO Standalone Stack via Docker Compose..." -ForegroundColor Cyan
    docker compose -f docker-compose.uloo.yaml up -d
    Write-Host "Docker containers started! Access URL: http://localhost:8200" -ForegroundColor Green
}

function Stop-DockerMode {
    Write-Host "Stopping ULOO Docker containers..." -ForegroundColor Yellow
    docker compose -f docker-compose.uloo.yaml down
    Write-Host "Docker containers stopped." -ForegroundColor Green
}

function Start-LocalMode {
    Write-Host "Starting local clean mode (Skip Dify Web and API, Core 8200 only)..." -ForegroundColor Cyan
    & .\scripts\dev-up.ps1 -SkipDifyApi -SkipWeb
    Write-Host "ULOO Standalone Engine ready! Access URL: http://localhost:8200" -ForegroundColor Green
}

function Stop-LocalProcesses {
    Write-Host "Stopping local Core 8200 processes..." -ForegroundColor Yellow
    $conns = Get-NetTCPConnection -LocalPort 8200 -ErrorAction SilentlyContinue
    if ($conns) {
        $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($p in $pids) {
            Write-Host "Terminating PID $p"
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "Port 8200 stopped." -ForegroundColor Green
}

if ($Action -eq "up") {
    if ($Docker) { Start-DockerMode } else { Start-LocalMode }
}
elseif ($Action -eq "down") {
    if ($Docker) { Stop-DockerMode } else { Stop-LocalProcesses }
}
elseif ($Action -eq "status") {
    Write-Host "--- ULOO Port and Probe Status ---" -ForegroundColor Cyan
    $conn = Get-NetTCPConnection -LocalPort 8200 -ErrorAction SilentlyContinue
    if ($conn) {
        Write-Host "Core 8200: Running (PID: $($conn.OwningProcess[0]))" -ForegroundColor Green
        try {
            $resp = Invoke-RestMethod -Uri "http://localhost:8200/api/v1/health/ready" -Method Get -TimeoutSec 3
            Write-Host "Health Ready: $($resp | ConvertTo-Json -Compress)" -ForegroundColor Green
        } catch {
            Write-Host "Health Ready probe failed: $_" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Core 8200: Not running" -ForegroundColor Gray
    }
}
elseif ($Action -eq "restart") {
    Stop-LocalProcesses
    Start-Sleep -Seconds 2
    Start-LocalMode
}
