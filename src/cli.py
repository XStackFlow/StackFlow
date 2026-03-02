"""
StackFlow CLI — start the API server and/or the visual editor.

Usage:
  stackflow [start] [--api-only | --editor-only]
  stackflow pm <list|install|uninstall|info> [args...]
  stackflow install                              # re-run setup
"""

import argparse
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# ANSI colour helpers
RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
DIM    = "\033[2m"

def _c(code: str, text: str) -> str:
    return f"{code}{text}{RESET}"


BANNER = f"""
{BOLD}{_c(CYAN, "  ┌─┐┌┬┐┌─┐┌─┐┬┌─┌─┐┬  ┌─┐┬ ┬")}
{_c(CYAN, "  └─┐ │ ├─┤│  ├┴┐├┤ │  │ ││││")}
{_c(CYAN, "  └─┘ ┴ ┴ ┴└─┘┴ ┴└  ┴─┘└─┘└┴┘")}{RESET}
"""


def _stream(pipe, prefix: str, color: str) -> None:
    """Read lines from *pipe* and print them prefixed + coloured."""
    try:
        for raw in iter(pipe.readline, ""):
            line = raw.rstrip("\n")
            if line:
                print(f"{color}{prefix}{RESET} {line}", flush=True)
    except ValueError:
        pass  # pipe closed
    finally:
        pipe.close()


def _wait_any(procs: list) -> int:
    """Block until the first process in *procs* exits; return its index."""
    done = threading.Event()
    results: list[int | None] = [None] * len(procs)

    def _watch(i: int, p) -> None:
        p.wait()
        results[i] = p.returncode
        done.set()

    watchers = [threading.Thread(target=_watch, args=(i, p), daemon=True)
                for i, p in enumerate(procs)]
    for w in watchers:
        w.start()
    done.wait()
    return next(i for i, r in enumerate(results) if r is not None)


def cmd_start(api_only: bool = False, editor_only: bool = False) -> None:
    print(BANNER)

    venv_python = PROJECT_ROOT / "venv" / "bin" / "python"
    if not venv_python.exists():
        print(_c(RED, "✗ venv not found.  Run: stackflow install"))
        sys.exit(1)

    api_env = {
        **os.environ,
        "PYTHONPATH": str(PROJECT_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "PYTHONUNBUFFERED": "1",
    }

    procs: list[subprocess.Popen] = []
    threads: list[threading.Thread] = []

    if not editor_only:
        print(_c(CYAN,   "  → API     ") + "http://localhost:8000")
        api_proc = subprocess.Popen(
            [str(venv_python), "src/api_server.py"],
            cwd=str(PROJECT_ROOT),
            env=api_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        procs.append(api_proc)
        t = threading.Thread(target=_stream, args=(api_proc.stdout, "[api]   ", CYAN), daemon=True)
        t.start()
        threads.append(t)

    if not api_only:
        print(_c(GREEN,  "  → Editor  ") + "http://localhost:5173")
        editor_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(PROJECT_ROOT / "litegraph-editor"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        procs.append(editor_proc)
        t = threading.Thread(target=_stream, args=(editor_proc.stdout, "[editor]", GREEN), daemon=True)
        t.start()
        threads.append(t)

    print()

    def _shutdown(sig=None, frame=None) -> None:
        print(f"\n{_c(YELLOW, '  Shutting down…')}")
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Exit when the first process dies unexpectedly
    idx = _wait_any(procs)
    exited = procs[idx]
    name = "[api]" if (not editor_only and idx == 0) else "[editor]"
    print(_c(RED, f"\n  {name} exited with code {exited.returncode}. Stopping all processes."))
    _shutdown()


def cmd_pm(args: list[str]) -> None:
    """Proxy to src/pm.py."""
    venv_python = PROJECT_ROOT / "venv" / "bin" / "python"
    result = subprocess.run(
        [str(venv_python), "-m", "src.pm"] + args,
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")},
    )
    sys.exit(result.returncode)


def cmd_install() -> None:
    """Re-run the install script."""
    install_script = PROJECT_ROOT / "install.sh"
    if not install_script.exists():
        print(_c(RED, "✗ install.sh not found in project root."))
        sys.exit(1)
    result = subprocess.run(
        ["bash", str(install_script)],
        cwd=str(PROJECT_ROOT),
    )
    sys.exit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="stackflow",
        description="StackFlow CLI — manage your StackFlow instance.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  stackflow                    # start everything\n"
            "  stackflow start --api-only   # API server only\n"
            "  stackflow start --editor-only# visual editor only\n"
            "  stackflow pm list            # list modules\n"
            "  stackflow install            # re-run setup\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    start = subparsers.add_parser("start", help="Start the API server and/or the visual editor")
    group = start.add_mutually_exclusive_group()
    group.add_argument("--api-only",    action="store_true", help="Start only the API server")
    group.add_argument("--editor-only", action="store_true", help="Start only the visual editor")

    subparsers.add_parser("pm", help="Package manager commands (proxied to src/pm.py)")
    subparsers.add_parser("install", help="Re-run the setup/install script")

    # Collect anything after "pm" as passthrough args
    args, remainder = parser.parse_known_args()

    if args.command == "pm":
        cmd_pm(remainder)
    elif args.command == "install":
        cmd_install()
    elif args.command == "start":
        cmd_start(api_only=args.api_only, editor_only=args.editor_only)
    else:
        # Default: start everything
        cmd_start()


if __name__ == "__main__":
    main()
