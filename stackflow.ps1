#Requires -Version 5.1
<#
.SYNOPSIS
    StackFlow CLI - hybrid mode: Docker for infra, local for API + editor.
.DESCRIPTION
    Windows PowerShell equivalent of the Unix stackflow CLI.
    Commands: start, stop, restart, docker, logs, pm, install
.EXAMPLE
    .\stackflow.ps1 start
    .\stackflow.ps1 stop all
    .\stackflow.ps1 logs api -f
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Ensure UTF-8 output so log files with Unicode render correctly
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding           = [System.Text.Encoding]::UTF8

$SCRIPT_DIR = Split-Path -Parent $PSCommandPath
Set-Location $SCRIPT_DIR

# ── Ensure PATH includes common install locations (needed with -NoProfile) ─
$extraPaths = @(
    "${env:ProgramFiles}\Docker\Docker\resources\bin"
    "${env:ProgramFiles}\Docker\Docker"
    "${env:LOCALAPPDATA}\Programs\Python\Python313"
    "${env:LOCALAPPDATA}\Programs\Python\Python313\Scripts"
    "${env:ProgramFiles}\nodejs"
)
foreach ($p in $extraPaths) {
    if ((Test-Path $p) -and ($env:Path -notlike "*$p*")) {
        $env:Path = "$p;$env:Path"
    }
}
# Also pull in the full user+machine PATH in case -NoProfile dropped it
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath    = [Environment]::GetEnvironmentVariable('Path', 'User')
foreach ($segment in ($machinePath + ';' + $userPath).Split(';')) {
    if ($segment -and ($env:Path -notlike "*$segment*")) {
        $env:Path = "$env:Path;$segment"
    }
}

# ── Resolve compose command ───────────────────────────────────────────
$DC = "docker compose"
$savedEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$null = & docker compose version 2>$null
if ($LASTEXITCODE -ne 0) { $DC = "docker-compose" }
$ErrorActionPreference = $savedEAP

# ── Colours ────────────────────────────────────────────────────────────
function Write-Info  { param([string]$Msg) Write-Host "  -> " -ForegroundColor Cyan -NoNewline; Write-Host $Msg }
function Write-Ok    { param([string]$Msg) Write-Host "  [ok] " -ForegroundColor Green -NoNewline; Write-Host $Msg }
function Write-Warn  { param([string]$Msg) Write-Host "  [!] " -ForegroundColor Yellow -NoNewline; Write-Host $Msg }

$PIDFILE = Join-Path $SCRIPT_DIR ".stackflow.pids"
$LOGDIR  = Join-Path $SCRIPT_DIR "logs"
$VENV_PYTHON = Join-Path $SCRIPT_DIR "venv\Scripts\python.exe"

function Show-Usage {
    Write-Host ""
    Write-Host "  +-++-++-++-++-++-++-++-++-+" -ForegroundColor Cyan
    Write-Host "  |S||t||a||c||k||F||l||o||w|" -ForegroundColor Cyan
    Write-Host "  +-++-++-++-++-++-++-++-++-+" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Usage: " -ForegroundColor White -NoNewline; Write-Host "stackflow <command>"
    Write-Host ""
    Write-Host "  Commands:" -ForegroundColor White
    Write-Host "    start            " -ForegroundColor Cyan -NoNewline; Write-Host "Start infra + API + editor (default)"
    Write-Host "    stop             " -ForegroundColor Cyan -NoNewline; Write-Host "Stop API + editor (infra keeps running)"
    Write-Host "    stop all         " -ForegroundColor Cyan -NoNewline; Write-Host "Stop everything including Docker infra"
    Write-Host "    restart [ui]     " -ForegroundColor Cyan -NoNewline; Write-Host "Restart the API server (or UI with 'ui')"
    Write-Host "    docker           " -ForegroundColor Cyan -NoNewline; Write-Host "Show infra container status"
    Write-Host "    logs [api|ui] [-f]" -ForegroundColor Cyan -NoNewline; Write-Host "  Show logs"
    Write-Host "    pm <command>     " -ForegroundColor Cyan -NoNewline; Write-Host "Package manager (list, install, uninstall, info)"
    Write-Host "    install          " -ForegroundColor Cyan -NoNewline; Write-Host "Re-run the setup script"
    Write-Host ""
}

function Test-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    $savedPref = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $null = & docker info 2>$null
    $result = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $savedPref
    return $result
}

function Start-DockerDesktop {
    $dockerDesktop = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerDesktop) {
        Start-Process $dockerDesktop
        Write-Info "Waiting for Docker Desktop to start..."
        $maxWait = 60; $waited = 0
        while ($waited -lt $maxWait) {
            Start-Sleep -Seconds 3; $waited += 3
            if (Test-DockerReady) {
                Write-Ok "Docker Desktop is running"
                return
            }
            Write-Host "." -NoNewline
        }
        Write-Host ""
        Write-Warn "Docker Desktop did not start in time. Please start it manually."
    } else {
        Write-Warn "Docker Desktop not found. Please install and start Docker Desktop."
    }
}

function Ensure-Infra {
    if (-not (Test-DockerReady)) {
        Start-DockerDesktop
        if (-not (Test-DockerReady)) { return }
    }

    # Start Docker infra if not already running
    $savedPref = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $running = & docker compose ps 2>$null | Select-String "running"
    if (-not $running) {
        Write-Info "Starting infrastructure services..."
        & docker compose up -d --quiet-pull 2>$null
        if ($LASTEXITCODE -ne 0) {
            & docker compose up -d 2>$null
        }
    }
    $ErrorActionPreference = $savedPref
}

function Wait-ForLangfuse {
    $url = "http://localhost:3000/api/public/health"
    $maxAttempts = 30; $attempt = 0
    Write-Host "  -> Waiting for Langfuse..." -ForegroundColor Cyan -NoNewline
    while ($attempt -lt $maxAttempts) {
        try {
            $null = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
            Write-Host " ready" -ForegroundColor Green
            return
        } catch {}
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 2
        $attempt++
    }
    Write-Host " timed out (API will retry on its own)" -ForegroundColor Yellow
}

function Stop-ByPort {
    param([int]$Port)
    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
            Where-Object { $_.State -eq 'Listen' }
        foreach ($conn in $connections) {
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    } catch {}
}

function Start-ApiServer {
    if (-not (Test-Path $LOGDIR)) { New-Item -ItemType Directory -Path $LOGDIR -Force | Out-Null }
    $apiLog = Join-Path $LOGDIR "api.log"

    $env:PYTHONPATH = $SCRIPT_DIR
    $env:PYTHONUNBUFFERED = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUTF8 = "1"

    $proc = Start-Process -FilePath $VENV_PYTHON `
        -ArgumentList "src/api_server.py" `
        -WorkingDirectory $SCRIPT_DIR `
        -RedirectStandardOutput $apiLog `
        -RedirectStandardError (Join-Path $LOGDIR "api.err.log") `
        -WindowStyle Hidden `
        -PassThru

    $proc.Id | Out-File -Append -FilePath $PIDFILE
}

function Start-EditorServer {
    if (-not (Test-Path $LOGDIR)) { New-Item -ItemType Directory -Path $LOGDIR -Force | Out-Null }
    $editorLog = Join-Path $LOGDIR "editor.log"

    $npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if (-not $npmCmd) {
        Write-Warn "npm not found. Cannot start editor."
        return
    }

    $editorDir = Join-Path $SCRIPT_DIR "litegraph-editor"
    $proc = Start-Process -FilePath $npmCmd `
        -ArgumentList "run dev" `
        -WorkingDirectory $editorDir `
        -RedirectStandardOutput $editorLog `
        -RedirectStandardError (Join-Path $LOGDIR "editor.err.log") `
        -WindowStyle Hidden `
        -PassThru

    $proc.Id | Out-File -Append -FilePath $PIDFILE
}

function Stop-LocalProcesses {
    if (Test-Path $PIDFILE) {
        Get-Content $PIDFILE | ForEach-Object {
            $pid_ = $_.Trim()
            if ($pid_) {
                # Kill the process and its children
                try {
                    $proc = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
                    if ($proc) {
                        # Kill child processes first
                        Get-CimInstance Win32_Process |
                            Where-Object { $_.ParentProcessId -eq $pid_ } |
                            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
                        Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
                    }
                } catch {}
            }
        }
        Remove-Item $PIDFILE -Force -ErrorAction SilentlyContinue
    }
    # Belt and suspenders: kill anything on our ports
    Stop-ByPort 8000
    Stop-ByPort 5173
}

# ── Parse command ──────────────────────────────────────────────────────
$cmd  = if ($args.Count -ge 1) { $args[0] } else { "" }
[System.Collections.ArrayList]$rest = @()
if ($args.Count -ge 2) { $args[1..($args.Count - 1)] | ForEach-Object { $null = $rest.Add($_) } }

switch ($cmd) {
    { $_ -eq "start" -or $_ -eq "" } {
        Write-Host ""
        Write-Host "  +-++-++-++-++-++-++-++-++-+" -ForegroundColor Cyan
        Write-Host "  |S||t||a||c||k||F||l||o||w|" -ForegroundColor Cyan
        Write-Host "  +-++-++-++-++-++-++-++-++-+" -ForegroundColor Cyan
        Write-Host ""

        if (-not (Test-Path $VENV_PYTHON)) {
            Write-Host "  venv not found. Run: stackflow install" -ForegroundColor Red
            exit 1
        }

        # Stop any existing local processes
        Stop-LocalProcesses

        # Prepare log directory and truncate old logs
        if (-not (Test-Path $LOGDIR)) { New-Item -ItemType Directory -Path $LOGDIR -Force | Out-Null }
        "" | Set-Content (Join-Path $LOGDIR "api.log")
        "" | Set-Content (Join-Path $LOGDIR "editor.log")

        # Ensure Docker infra is up
        Ensure-Infra

        # Wait for Langfuse before starting API
        Wait-ForLangfuse

        # Start API + editor
        Start-ApiServer
        Start-EditorServer

        Write-Host ""
        Write-Host "  Services running:" -ForegroundColor Green
        Write-Host "    API      " -ForegroundColor Cyan -NoNewline; Write-Host "http://localhost:8000"
        Write-Host "    Editor   " -ForegroundColor Cyan -NoNewline; Write-Host "http://localhost:5173"
        Write-Host "    Langfuse " -ForegroundColor Cyan -NoNewline; Write-Host "http://localhost:3000  (admin@stackflow.local / adminadmin)"
        Write-Host ""
        Write-Host "  Use " -ForegroundColor DarkGray -NoNewline
        Write-Host "stackflow logs -f" -ForegroundColor Cyan -NoNewline
        Write-Host " to follow logs" -ForegroundColor DarkGray
        Write-Host "  Use " -ForegroundColor DarkGray -NoNewline
        Write-Host "stackflow stop" -ForegroundColor Cyan -NoNewline
        Write-Host " to stop services" -ForegroundColor DarkGray
        Write-Host ""
    }

    "stop" {
        Stop-LocalProcesses
        if ($rest -contains "all") {
            Write-Info "Stopping Docker infrastructure..."
            $savedPref = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
            & docker compose down 2>$null
            $ErrorActionPreference = $savedPref
            Write-Ok "Everything stopped"
        } else {
            Write-Ok "API + editor stopped"
        }
    }

    "restart" {
        $target = if ($rest.Count -ge 1) { $rest[0] } else { "api" }
        switch ($target) {
            { $_ -eq "ui" -or $_ -eq "editor" } {
                Stop-ByPort 5173
                Start-Sleep -Seconds 2
                try { "" | Set-Content (Join-Path $LOGDIR "editor.log") } catch {}
                Start-EditorServer
                Write-Ok "Editor restarted"
            }
            default {
                Stop-ByPort 8000
                Start-Sleep -Seconds 2
                try { "" | Set-Content (Join-Path $LOGDIR "api.log") } catch {}
                Start-ApiServer
                Write-Ok "API restarted"
            }
        }
    }

    "docker" {
        $savedPref = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        & docker compose ps
        $ErrorActionPreference = $savedPref
    }

    "logs" {
        $follow = $false
        $target = ""
        foreach ($arg in $rest) {
            switch ($arg) {
                { $_ -eq "--follow" -or $_ -eq "-f" } { $follow = $true }
                "api"     { $target = "api" }
                { $_ -eq "editor" -or $_ -eq "ui" } { $target = "editor" }
            }
        }

        $logFile = switch ($target) {
            "editor" { Join-Path $LOGDIR "editor.log" }
            default  { Join-Path $LOGDIR "api.log" }
        }

        if (-not (Test-Path $logFile)) {
            Write-Warn "No log files found. Run 'stackflow start' first."
        } elseif ($follow) {
            Get-Content -Path $logFile -Tail 50 -Wait -Encoding UTF8
        } else {
            Get-Content -Path $logFile -Tail 50 -Encoding UTF8
        }
    }

    "pm" {
        $env:PYTHONPATH = $SCRIPT_DIR
        & $VENV_PYTHON -m src.pm @rest
    }

    "install" {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $SCRIPT_DIR "install.ps1")
    }

    { $_ -eq "help" -or $_ -eq "--help" -or $_ -eq "-h" } {
        Show-Usage
    }

    default {
        Write-Host "  Unknown command: $cmd" -ForegroundColor Yellow
        Show-Usage
        exit 1
    }
}
