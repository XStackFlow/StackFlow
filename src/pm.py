"""StackFlow Package Manager CLI.

Usage:
  python -m src.pm list              # show all modules and install status
  python -m src.pm install <name>    # install a module (runs setup)
  python -m src.pm install all       # install all modules
  python -m src.pm uninstall <name>  # remove from installed list
  python -m src.pm info <name>       # show module details
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Suppress the full logging setup (with file handler + init banner) when running
# as a CLI tool.  A WARNING-level root handler is enough for pm commands.
logging.basicConfig(level=logging.WARNING, format="%(message)s")

PROJECT_ROOT = Path(__file__).parent.parent
MODULES_DIR = PROJECT_ROOT / "modules"


def _load_env_from(path: Path) -> dict[str, str]:
    """Read a .env file into a dict."""
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def _write_module_env(module_name: str, key: str, value: str) -> None:
    """Write a key=value entry to modules/<name>/.env."""
    env_path = MODULES_DIR / module_name / ".env"
    with open(env_path, "a") as f:
        f.write(f"{key}={value}\n")


def _color(text: str, code: str) -> str:
    """Wrap text in ANSI color codes."""
    return f"\033[{code}m{text}\033[0m"


def _green(t): return _color(t, "32")
def _yellow(t): return _color(t, "33")
def _red(t): return _color(t, "31")
def _bold(t): return _color(t, "1")
def _cyan(t): return _color(t, "36")
def _dim(t): return _color(t, "2")


def cmd_list():
    """List all available modules with their install status."""
    from src.utils.setup.module_registry import get_all_manifests, get_installed_modules
    manifests = get_all_manifests()
    installed = set(get_installed_modules())

    print()
    print(_bold("  StackFlow Modules"))
    print("  " + "─" * 56)
    print(f"  {'Module':<14} {'Status':<14} Description")
    print("  " + "─" * 56)

    for module_id, manifest in manifests.items():
        status = _green("✓ installed") if module_id in installed else _dim("  not installed")
        name = manifest.get("name", module_id)
        desc = manifest.get("description", "")
        print(f"  {name:<14} {status:<23} {_dim(desc)}")

    print()
    installed_count = len(installed)
    total = len(manifests)
    print(f"  {installed_count}/{total} modules installed")
    print()


def cmd_info(name: str):
    """Show detailed info about a module."""
    from src.utils.setup.module_registry import get_manifest, get_installed_modules
    try:
        manifest = get_manifest(name)
    except FileNotFoundError as e:
        print(_red(f"  Error: {e}"))
        sys.exit(1)

    installed = name in get_installed_modules()
    status = _green("installed") if installed else _yellow("not installed")

    print()
    print(_bold(f"  {manifest['name']} v{manifest['version']}"))
    print(f"  Status:      {status}")
    print(f"  Description: {manifest.get('description', '')}")
    print(f"  Nodes:       {', '.join(manifest.get('nodes', []))}")

    setup = manifest.get("setup", {})
    env_vars = setup.get("env_vars", [])
    if env_vars:
        print(f"  Env vars:    {', '.join(env_vars)}")

    steps = setup.get("steps", [])
    if steps:
        print(f"  Setup steps:")
        for step in steps:
            stype = step.get("type")
            if stype == "check_command":
                print(f"    - Check command: {step['command']}")
            elif stype == "run_command":
                print(f"    - Run: {step['command']}")
    print()


def _run_setup(manifest: dict) -> bool:
    """Run the setup steps for a module. Returns True if successful."""
    setup = manifest.get("setup", {})
    steps = setup.get("steps", [])
    env_vars = setup.get("env_vars", [])
    name = manifest.get("name", manifest["id"])

    # Process setup steps
    for step in steps:
        stype = step.get("type")

        if stype == "check_command":
            cmd = step["command"]
            if not shutil.which(cmd):
                print(_yellow(f"\n  ⚠  '{cmd}' is not installed."))
                print(f"     {step.get('message', '')}")
                hints = step.get("install_hint", {})
                if hints:
                    platform = sys.platform
                    if platform == "darwin" and "macos" in hints:
                        print(f"     Install: {_cyan(hints['macos'])}")
                    elif "linux" in hints:
                        print(f"     Install: {_cyan(hints['linux'])}")
                answer = input("\n  Continue anyway? [y/N] ").strip().lower()
                if answer != "y":
                    print(_red("  Aborted."))
                    return False

        elif stype == "run_command":
            cmd_str = step["command"]
            msg = step.get("message", f"Running: {cmd_str}")
            interactive = step.get("interactive", False)
            print(f"\n  {msg}")
            if interactive:
                result = subprocess.run(cmd_str, shell=True)
            else:
                result = subprocess.run(cmd_str, shell=True, capture_output=False)
            if result.returncode != 0:
                answer = input(_yellow(f"\n  Command exited with code {result.returncode}. Continue? [y/N] ")).strip().lower()
                if answer != "y":
                    print(_red("  Aborted."))
                    return False

    # Handle env vars — stored in modules/<name>/.env
    if env_vars:
        module_id = manifest.get("id", name)
        module_env = _load_env_from(MODULES_DIR / module_id / ".env")
        os_env = os.environ
        print()
        for var in env_vars:
            existing = module_env.get(var) or os_env.get(var)
            if existing:
                print(f"  {_green('✓')} {var} already set")
            else:
                value = input(f"  Enter value for {_bold(var)} (or press Enter to skip): ").strip()
                if value:
                    _write_module_env(module_id, var, value)
                    print(f"  {_green('✓')} {var} written to modules/{module_id}/.env")
                else:
                    print(f"  {_yellow('⚠')}  {var} skipped — set it later in modules/{module_id}/.env")

    return True


def cmd_install(name: str):
    """Install a module or all modules."""
    from src.utils.setup.module_registry import (
        get_all_manifests, get_installed_modules, get_manifest, install_module
    )

    if name == "all":
        manifests = get_all_manifests()
        for module_id in manifests:
            cmd_install(module_id)
        return

    # Validate module exists
    try:
        manifest = get_manifest(name)
    except FileNotFoundError:
        print(_red(f"  Error: Unknown module '{name}'."))
        print(f"  Run {_cyan('python -m src.pm list')} to see available modules.")
        sys.exit(1)

    installed = get_installed_modules()
    display_name = manifest.get("name", name)

    if name in installed:
        print(f"\n  {_green('✓')} {display_name} is already installed.")
        return

    print(f"\n  Installing {_bold(display_name)}...")
    print(f"  {_dim(manifest.get('description', ''))}")

    success = _run_setup(manifest)
    if not success:
        print()
        return

    # Install Python dependencies if the module has a requirements.txt
    req_file = PROJECT_ROOT / "modules" / name / "requirements.txt"
    if req_file.exists():
        print(f"\n  Installing Python dependencies…")
        if sys.platform == "win32":
            venv_pip = PROJECT_ROOT / "venv" / "Scripts" / "pip.exe"
        else:
            venv_pip = PROJECT_ROOT / "venv" / "bin" / "pip"
        result = subprocess.run(
            [str(venv_pip), "install", "--quiet", "-r", str(req_file)],
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0:
            print(_red(f"  pip install failed (exit {result.returncode})"))
            print()
            return
        print(f"  {_green('✓')} Dependencies installed")

    install_module(name)
    print(f"\n  {_green('✓')} {display_name} installed successfully.")
    print()


def cmd_uninstall(name: str):
    """Remove a module from the installed list."""
    from src.utils.setup.module_registry import get_installed_modules, get_manifest, uninstall_module

    try:
        manifest = get_manifest(name)
    except FileNotFoundError:
        print(_red(f"  Error: Unknown module '{name}'."))
        sys.exit(1)

    installed = get_installed_modules()
    display_name = manifest.get("name", name)

    if name not in installed:
        print(f"\n  {_yellow('⚠')}  {display_name} is not installed.")
        return

    uninstall_module(name)
    print(f"\n  {_green('✓')} {display_name} uninstalled.")
    print()


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    command = args[0]

    if command == "list":
        cmd_list()
    elif command == "install":
        if len(args) < 2:
            print(_red("  Error: specify a module name or 'all'."))
            sys.exit(1)
        cmd_install(args[1])
    elif command == "uninstall":
        if len(args) < 2:
            print(_red("  Error: specify a module name."))
            sys.exit(1)
        cmd_uninstall(args[1])
    elif command == "info":
        if len(args) < 2:
            print(_red("  Error: specify a module name."))
            sys.exit(1)
        cmd_info(args[1])
    else:
        print(_red(f"  Unknown command: {command}"))
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
