#!/usr/bin/env bash
# StackFlow — one-command install script.
# Installs everything: Homebrew, Python, Node, Docker (Colima), infra services.
# API + editor run locally; Langfuse + PostgreSQL run in Docker.
#
# Usage:
#   ./install.sh                              (from inside a clone)
#   bash -c "$(curl -sSL <raw-url>)"          (from anywhere — clones the repo)
set -euo pipefail

# ── Windows detection ──────────────────────────────────────────────────
case "$(uname -s 2>/dev/null || echo Unknown)" in
    MINGW*|MSYS*|CYGWIN*|Windows_NT)
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        printf "\n  Detected Windows — launching PowerShell installer…\n\n"
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$SCRIPT_DIR/install.ps1"
        exit $?
        ;;
esac

# ── Resolve docker compose command ───────────────────────────────────
resolve_dc() {
    if docker compose version &>/dev/null; then
        DC="docker compose"
    else
        DC="docker-compose"
    fi
}

# ── Colours ──────────────────────────────────────────────────────────
RESET="\033[0m"; BOLD="\033[1m"; CYAN="\033[36m"; GREEN="\033[32m"
YELLOW="\033[33m"; RED="\033[31m"; DIM="\033[2m"

info()  { printf "${CYAN}  →${RESET} %b\n" "$*"; }
ok()    { printf "${GREEN}  ✓${RESET} %b\n" "$*"; }
warn()  { printf "${YELLOW}  ⚠${RESET}  %b\n" "$*"; }
fail()  { printf "${RED}  ✗${RESET} %b\n" "$*"; exit 1; }

# ── Banner ───────────────────────────────────────────────────────────
banner() {
    printf "\n"
    printf "${BOLD}${CYAN}  ┌─┐┌┬┐┌─┐┌─┐┬┌─┌─┐┬  ┌─┐┬ ┬${RESET}\n"
    printf "${CYAN}  └─┐ │ ├─┤│  ├┴┐├┤ │  │ ││││${RESET}\n"
    printf "${CYAN}  └─┘ ┴ ┴ ┴└─┘┴ ┴└  ┴─┘└─┘└┴┘${RESET}\n"
    printf "\n"
    printf "  ${DIM}One-command installer${RESET}\n\n"
}

# ── Prompt helper ────────────────────────────────────────────────────
ask() {
    local var="$1" prompt="$2" default="${3:-}"
    if [[ -n "$default" ]]; then
        printf "  ${BOLD}%s${RESET} ${DIM}[%s]${RESET}: " "$prompt" "$default"
    else
        printf "  ${BOLD}%s${RESET}: " "$prompt"
    fi
    read -r value
    value="${value:-$default}"
    eval "$var=\"\$value\""
}

# ── Homebrew ─────────────────────────────────────────────────────────
ensure_brew() {
    if command -v brew &>/dev/null; then
        ok "Homebrew"
        return
    fi
    info "Installing Homebrew…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -f /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    ok "Homebrew installed"
}

# ── Install missing tools via brew ───────────────────────────────────
ensure_prereqs() {
    info "Checking prerequisites…"

    # Python 3.11+
    local need_python=0
    if command -v python3 &>/dev/null; then
        local pyver pymajor pyminor
        pyver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        pymajor="${pyver%%.*}"
        pyminor="${pyver##*.}"
        if (( pymajor < 3 || (pymajor == 3 && pyminor < 11) )); then
            need_python=1
        else
            ok "Python $pyver"
        fi
    else
        need_python=1
    fi
    if (( need_python )); then
        info "Installing Python 3.13…"
        brew install python@3.13
        ok "Python 3.13"
    fi

    # Node.js (includes npm)
    if command -v node &>/dev/null; then
        ok "Node.js $(node --version)"
    else
        info "Installing Node.js…"
        brew install node
        ok "Node.js"
    fi

    # git
    if command -v git &>/dev/null; then
        ok "git"
    else
        info "Installing git…"
        brew install git
        ok "git"
    fi

    # Docker CLI
    if command -v docker &>/dev/null; then
        ok "Docker CLI"
    else
        info "Installing Docker CLI…"
        brew install docker
        ok "Docker CLI"
    fi

    # Docker Compose
    if docker compose version &>/dev/null || command -v docker-compose &>/dev/null; then
        ok "Docker Compose"
    else
        info "Installing Docker Compose…"
        brew install docker-compose
        ok "Docker Compose"
    fi

    # Docker Buildx (needed by compose build)
    if docker buildx version &>/dev/null; then
        ok "Docker Buildx"
    else
        info "Installing Docker Buildx…"
        brew install docker-buildx
        mkdir -p "$HOME/.docker/cli-plugins"
        ln -sfn "$(brew --prefix docker-buildx)/bin/docker-buildx" "$HOME/.docker/cli-plugins/docker-buildx"
        ok "Docker Buildx"
    fi

    printf "\n"
}

# ── Docker runtime (Colima or Docker Desktop) ────────────────────────
ensure_docker_runtime() {
    if docker info &>/dev/null 2>&1; then
        ok "Docker runtime already running"
        return
    fi

    if ! command -v colima &>/dev/null; then
        info "Installing Colima (lightweight Docker runtime)…"
        brew install colima
        ok "Colima installed"
    fi

    info "Starting Colima…"
    colima start --cpu 2 --memory 4 --disk 20 --vm-type=vz --vz-rosetta 2>/dev/null \
        || colima start --cpu 2 --memory 4 --disk 20 2>/dev/null \
        || colima start \
        || fail "Could not start Colima. Try manually: colima start"
    ok "Colima running"
    printf "\n"
}

# ── Determine STACKFLOW_HOME ────────────────────────────────────────
resolve_home() {
    if [[ -f "$(dirname "${BASH_SOURCE[0]}")/src/api_server.py" ]]; then
        STACKFLOW_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        info "Using existing repo at ${BOLD}$STACKFLOW_HOME${RESET}"
    else
        local default_home="$HOME/.stackflow"
        ask STACKFLOW_HOME "Install location" "$default_home"
        STACKFLOW_HOME="${STACKFLOW_HOME/#\~/$HOME}"

        if [[ -d "$STACKFLOW_HOME/src" ]]; then
            info "Existing installation found — updating."
            cd "$STACKFLOW_HOME"
            git pull --ff-only || warn "git pull failed — continuing with current version."
        else
            info "Cloning StackFlow into ${BOLD}$STACKFLOW_HOME${RESET}…"
            git clone --depth=1 https://github.com/StackAdapt/StackFlow.git "$STACKFLOW_HOME"
        fi
    fi
    cd "$STACKFLOW_HOME"
    printf "\n"
}

# ── Python venv + deps ──────────────────────────────────────────────
setup_python() {
    info "Setting up Python environment…"
    if [[ ! -d "venv" ]]; then
        python3 -m venv venv
        ok "Created venv"
    else
        ok "venv already exists"
    fi

    info "Installing Python dependencies (this may take a minute)…"
    venv/bin/pip install --quiet --upgrade pip
    venv/bin/pip install --quiet -r requirements.txt
    ok "Python dependencies installed"
    printf "\n"
}

# ── Node deps ────────────────────────────────────────────────────────
setup_node() {
    info "Installing Node.js dependencies…"
    (cd litegraph-editor && npm install --silent)
    ok "Node.js dependencies installed"
    printf "\n"
}

# ── Docker infra services (Langfuse + PostgreSQL) ────────────────────
setup_infra() {
    info "Starting infrastructure services (Langfuse + PostgreSQL)…"

    $DC up -d 2>&1 | while IFS= read -r line; do
        printf "  ${DIM}%s${RESET}\n" "$line"
    done

    ok "Infrastructure services running"
    printf "  ${DIM}Langfuse:   http://localhost:3000  (admin@stackflow.local / adminadmin)${RESET}\n"
    printf "  ${DIM}PostgreSQL: localhost:5432${RESET}\n"
    printf "\n"
}

# ── Interactive .env setup ───────────────────────────────────────────
setup_env() {
    if [[ -f ".env" ]]; then
        warn ".env already exists — skipping interactive setup."
        printf "  ${DIM}To reconfigure, delete .env and re-run install.sh${RESET}\n\n"
        return
    fi

    info "Writing .env…"
    cat > .env <<ENVFILE
# Langfuse Configuration (local Docker instance)
LANGFUSE_PUBLIC_KEY=pk-lf-local
LANGFUSE_SECRET_KEY=sk-lf-local
LANGFUSE_HOST=http://localhost:3000

# Database Configuration (Docker PostgreSQL — shared with Langfuse)
LANGGRAPH_DB_USER=postgres
LANGGRAPH_DB_PASSWORD=postgres
LANGGRAPH_DB_NAME=stackflow
LANGGRAPH_DB_HOST=localhost
LANGGRAPH_DB_PORT=5432

# Langgraph config
MAX_CONCURRENCY=3
RECURSION_LIMIT=10000
ENVFILE
    ok ".env created"
    printf "\n"
}

# ── Install global CLI ──────────────────────────────────────────────
install_cli() {
    info "Installing ${BOLD}stackflow${RESET} command…"

    chmod +x "$STACKFLOW_HOME/stackflow"

    local bin_dir="$HOME/.local/bin"
    mkdir -p "$bin_dir"

    cat > "$bin_dir/stackflow" <<WRAPPER
#!/usr/bin/env bash
# Auto-generated by StackFlow installer — $(date +%Y-%m-%d)
exec "$STACKFLOW_HOME/stackflow" "\$@"
WRAPPER
    chmod +x "$bin_dir/stackflow"

    if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
        warn "$bin_dir is not in your PATH."
        printf "  ${DIM}Add this to your shell profile (~/.zshrc or ~/.bashrc):${RESET}\n"
        printf "  ${CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}\n\n"
    else
        ok "stackflow command installed to $bin_dir/stackflow"
    fi
    printf "\n"
}

# ── Success ──────────────────────────────────────────────────────────
done_msg() {
    printf "${GREEN}${BOLD}  ✓ Setup complete!${RESET}\n\n"
    printf "  ${BOLD}Commands:${RESET}\n"
    printf "    ${CYAN}stackflow${RESET}              Start API + editor\n"
    printf "    ${CYAN}stackflow stop${RESET}         Stop API + editor\n"
    printf "    ${CYAN}stackflow docker${RESET}       Show infra container status\n"
    printf "    ${CYAN}stackflow logs -f${RESET}      Tail API logs\n"
    printf "    ${CYAN}stackflow pm list${RESET}      List modules\n"
    printf "    ${CYAN}stackflow install${RESET}      Re-run setup\n"
    printf "\n"
}

# ── Main ─────────────────────────────────────────────────────────────
banner
ensure_brew
ensure_prereqs
resolve_home
setup_python
setup_node
setup_env
ensure_docker_runtime
resolve_dc
setup_infra
install_cli
done_msg
