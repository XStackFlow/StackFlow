#Requires -Version 5.1
<#
.SYNOPSIS
    StackFlow - one-command install script for Windows.
.DESCRIPTION
    Installs everything: Python, Node.js, Git, Docker Desktop, infra services.
    API + editor run locally; Langfuse + PostgreSQL run in Docker.
.EXAMPLE
    .\install.ps1                 # from inside a clone
    irm <raw-url> | iex          # from anywhere (clones the repo)
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Colours ────────────────────────────────────────────────────────────
function Write-Info  { param([string]$Msg) Write-Host "  -> " -ForegroundColor Cyan -NoNewline; Write-Host $Msg }
function Write-Ok    { param([string]$Msg) Write-Host "  [ok] " -ForegroundColor Green -NoNewline; Write-Host $Msg }
function Write-Warn  { param([string]$Msg) Write-Host "  [!] " -ForegroundColor Yellow -NoNewline; Write-Host $Msg }
function Write-Fail  { param([string]$Msg) Write-Host "  [x] " -ForegroundColor Red -NoNewline; Write-Host $Msg; exit 1 }

# ── Banner ─────────────────────────────────────────────────────────────
function Show-Banner {
    Write-Host ""
    Write-Host "  +-++-++-++-++-++-++-++-++-+" -ForegroundColor Cyan
    Write-Host "  |S||t||a||c||k||F||l||o||w|" -ForegroundColor Cyan
    Write-Host "  +-++-++-++-++-++-++-++-++-+" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  One-command installer (Windows)" -ForegroundColor DarkGray
    Write-Host ""
}

# ── Prompt helper ──────────────────────────────────────────────────────
function Read-Default {
    param([string]$Prompt, [string]$Default)
    if ($Default) {
        $value = Read-Host "  $Prompt [$Default]"
        if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
        return $value
    }
    return Read-Host "  $Prompt"
}

# ── Refresh PATH within current session ────────────────────────────────
function Update-SessionPath {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machinePath;$userPath"
}

# ── Check for winget ───────────────────────────────────────────────────
function Ensure-Winget {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Ok "winget"
        return
    }
    Write-Fail "winget is not available. Please install App Installer from the Microsoft Store."
}

# ── Prerequisites ──────────────────────────────────────────────────────
function Ensure-Prereqs {
    Write-Info "Checking prerequisites..."

    # Python 3.11+
    $needPython = $true
    if (Get-Command python -ErrorAction SilentlyContinue) {
        try {
            $pyver = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($pyver) {
                $parts = $pyver.Split('.')
                $major = [int]$parts[0]; $minor = [int]$parts[1]
                if ($major -ge 3 -and $minor -ge 11) {
                    Write-Ok "Python $pyver"
                    $needPython = $false
                }
            }
        } catch {}
    }
    if ($needPython) {
        Write-Info "Installing Python 3.13..."
        winget install --id Python.Python.3.13 --accept-source-agreements --accept-package-agreements --silent
        Update-SessionPath
        if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
            Write-Fail "Python installation failed. Please install Python 3.11+ manually."
        }
        Write-Ok "Python 3.13"
    }

    # Node.js
    if (Get-Command node -ErrorAction SilentlyContinue) {
        Write-Ok "Node.js $(node --version)"
    } else {
        Write-Info "Installing Node.js..."
        winget install --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements --silent
        Update-SessionPath
        if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
            Write-Fail "Node.js installation failed. Please install Node.js manually."
        }
        Write-Ok "Node.js"
    }

    # Git
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Ok "git"
    } else {
        Write-Info "Installing Git..."
        winget install --id Git.Git --accept-source-agreements --accept-package-agreements --silent
        Update-SessionPath
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            Write-Fail "Git installation failed. Please install Git manually."
        }
        Write-Ok "git"
    }

    # Docker Desktop (replaces Docker CLI + Colima on macOS)
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Write-Ok "Docker CLI"
    } else {
        Write-Info "Installing Docker Desktop..."
        winget install --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements --silent
        Update-SessionPath
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            Write-Warn "Docker Desktop installed but may require a restart."
            Write-Warn "Please restart your computer, then re-run this script."
            exit 0
        }
        Write-Ok "Docker Desktop"
    }

    # Docker Compose (bundled with Docker Desktop, but verify)
    try {
        $null = & docker compose version 2>$null
        Write-Ok "Docker Compose"
    } catch {
        Write-Warn "Docker Compose not found. It should be bundled with Docker Desktop."
        Write-Warn "Please ensure Docker Desktop is installed and running."
    }

    Write-Host ""
}

# ── Docker runtime ─────────────────────────────────────────────────────
function Test-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    $savedPref = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $null = & docker info 2>$null
    $result = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $savedPref
    return $result
}

function Ensure-DockerRuntime {
    # Check if Docker daemon is responsive
    if (Test-DockerReady) {
        Write-Ok "Docker runtime already running"
        return
    }

    Write-Info "Docker is not running. Attempting to start Docker Desktop..."

    # Try to start Docker Desktop
    $dockerDesktop = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerDesktop) {
        Start-Process $dockerDesktop
        Write-Info "Waiting for Docker Desktop to start (this may take a minute)..."

        $maxWait = 60
        $waited = 0
        while ($waited -lt $maxWait) {
            Start-Sleep -Seconds 3
            $waited += 3
            if (Test-DockerReady) {
                Write-Ok "Docker Desktop is running"
                Write-Host ""
                return
            }
            Write-Host "." -NoNewline
        }
        Write-Host ""
        Write-Fail "Docker Desktop did not start in time. Please start it manually and re-run this script."
    } else {
        Write-Fail "Docker Desktop not found. Please install and start Docker Desktop, then re-run this script."
    }
}

# ── Determine STACKFLOW_HOME ──────────────────────────────────────────
function Resolve-Home {
    $scriptDir = Split-Path -Parent $PSCommandPath

    if (Test-Path (Join-Path $scriptDir "src\api_server.py")) {
        $script:STACKFLOW_HOME = $scriptDir
        Write-Info "Using existing repo at $($script:STACKFLOW_HOME)"
    } else {
        $defaultHome = Join-Path $env:USERPROFILE ".stackflow"
        $script:STACKFLOW_HOME = Read-Default "Install location" $defaultHome

        if (Test-Path (Join-Path $script:STACKFLOW_HOME "src")) {
            Write-Info "Existing installation found - updating."
            Push-Location $script:STACKFLOW_HOME
            try { & git pull --ff-only } catch { Write-Warn "git pull failed - continuing with current version." }
            Pop-Location
        } else {
            Write-Info "Cloning StackFlow into $($script:STACKFLOW_HOME)..."
            & git clone --depth=1 https://github.com/StackAdapt/StackFlow.git $script:STACKFLOW_HOME
        }
    }

    Set-Location $script:STACKFLOW_HOME
    Write-Host ""
}

# ── Python venv + deps ─────────────────────────────────────────────────
function Setup-Python {
    Write-Info "Setting up Python environment..."

    if (-not (Test-Path "venv")) {
        & python -m venv venv
        Write-Ok "Created venv"
    } else {
        Write-Ok "venv already exists"
    }

    Write-Info "Installing Python dependencies (this may take a minute)..."
    & venv\Scripts\pip.exe install --quiet --upgrade pip
    & venv\Scripts\pip.exe install --quiet -r requirements.txt
    Write-Ok "Python dependencies installed"
    Write-Host ""
}

# ── Node deps ──────────────────────────────────────────────────────────
function Setup-Node {
    Write-Info "Installing Node.js dependencies..."
    Push-Location litegraph-editor
    & npm install --silent 2>$null
    Pop-Location
    Write-Ok "Node.js dependencies installed"
    Write-Host ""
}

# ── .env setup ─────────────────────────────────────────────────────────
function Setup-Env {
    if (Test-Path ".env") {
        Write-Warn ".env already exists - skipping interactive setup."
        Write-Host "  To reconfigure, delete .env and re-run install.ps1" -ForegroundColor DarkGray
        Write-Host ""
        return
    }

    Write-Info "Writing .env..."
    @"
# Langfuse Configuration (local Docker instance)
LANGFUSE_PUBLIC_KEY=pk-lf-local
LANGFUSE_SECRET_KEY=sk-lf-local
LANGFUSE_HOST=http://localhost:3000

# Database Configuration (Docker PostgreSQL - shared with Langfuse)
LANGGRAPH_DB_USER=postgres
LANGGRAPH_DB_PASSWORD=postgres
LANGGRAPH_DB_NAME=stackflow
LANGGRAPH_DB_HOST=localhost
LANGGRAPH_DB_PORT=5432

# Langgraph config
MAX_CONCURRENCY=3
RECURSION_LIMIT=10000
"@ | Set-Content -Path ".env" -Encoding UTF8
    Write-Ok ".env created"
    Write-Host ""
}

# ── Docker infra services ─────────────────────────────────────────────
function Setup-Infra {
    Write-Info "Starting infrastructure services (Langfuse + PostgreSQL)..."

    $savedPref = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & docker compose up -d 2>&1 | ForEach-Object {
        Write-Host "  $_" -ForegroundColor DarkGray
    }
    $ErrorActionPreference = $savedPref

    Write-Ok "Infrastructure services running"
    Write-Host "  Langfuse:   http://localhost:3000  (admin@stackflow.local / adminadmin)" -ForegroundColor DarkGray
    Write-Host "  PostgreSQL: localhost:5432" -ForegroundColor DarkGray
    Write-Host ""
}

# ── Install global CLI ─────────────────────────────────────────────────
function Install-Cli {
    Write-Info "Installing stackflow command..."

    # Create a stackflow.cmd wrapper in a directory on PATH
    $binDir = Join-Path $env:USERPROFILE ".local\bin"
    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }

    # Create .cmd wrapper for use from cmd.exe / PowerShell
    $cmdWrapper = Join-Path $binDir "stackflow.cmd"
    @"
@echo off
REM Auto-generated by StackFlow installer - $(Get-Date -Format 'yyyy-MM-dd')
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$($script:STACKFLOW_HOME)\stackflow.ps1" %*
"@ | Set-Content -Path $cmdWrapper -Encoding ASCII

    # Create .ps1 wrapper for direct PowerShell use
    $ps1Wrapper = Join-Path $binDir "stackflow.ps1"
    @"
# Auto-generated by StackFlow installer - $(Get-Date -Format 'yyyy-MM-dd')
& "$($script:STACKFLOW_HOME)\stackflow.ps1" @args
"@ | Set-Content -Path $ps1Wrapper -Encoding UTF8

    # Add to user PATH if not already there
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($userPath -notlike "*$binDir*") {
        [Environment]::SetEnvironmentVariable('Path', "$userPath;$binDir", 'User')
        $env:Path = "$env:Path;$binDir"
        Write-Ok "Added $binDir to user PATH"
        Write-Warn "You may need to restart your terminal for PATH changes to take effect."
    } else {
        Write-Ok "stackflow command installed to $binDir"
    }
    Write-Host ""
}

# ── Success ────────────────────────────────────────────────────────────
function Show-Done {
    Write-Host ""
    Write-Host "  [ok] Setup complete!" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Commands:" -ForegroundColor White
    Write-Host "    stackflow              " -ForegroundColor Cyan -NoNewline; Write-Host "Start API + editor"
    Write-Host "    stackflow stop         " -ForegroundColor Cyan -NoNewline; Write-Host "Stop API + editor"
    Write-Host "    stackflow docker       " -ForegroundColor Cyan -NoNewline; Write-Host "Show infra container status"
    Write-Host "    stackflow logs -f      " -ForegroundColor Cyan -NoNewline; Write-Host "Tail API logs"
    Write-Host "    stackflow pm list      " -ForegroundColor Cyan -NoNewline; Write-Host "List modules"
    Write-Host "    stackflow install      " -ForegroundColor Cyan -NoNewline; Write-Host "Re-run setup"
    Write-Host ""
}

# ── Main ───────────────────────────────────────────────────────────────
Show-Banner
Ensure-Winget
Ensure-Prereqs
Resolve-Home
Setup-Python
Setup-Node
Setup-Env
Ensure-DockerRuntime
Setup-Infra
Install-Cli
Show-Done
