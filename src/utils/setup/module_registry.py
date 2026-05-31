"""Module registry for StackFlow package manager.

Discovers available modules by scanning for manifest.json files in two directories:
  - modules/   built-in modules shipped with the repo (tracked by git)
  - installed/ externally installed modules (gitignored, auto-detected)

modules.json tracks which built-in modules are enabled.
Everything in installed/ with a manifest.json is always active.
"""

import json
import os
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
MODULES_DIR = PROJECT_ROOT / "modules"
INSTALLED_DIR = PROJECT_ROOT / "installed"
MODULES_JSON = PROJECT_ROOT / "modules.json"

# Per-hook wall-clock budget. A misbehaving module (e.g. one that blocks on a
# slow TCP timeout to an unreachable embedding host) must not stall the rest of
# server startup. Override with STACKFLOW_MODULE_HOOK_TIMEOUT.
_HOOK_TIMEOUT_S = float(os.environ.get("STACKFLOW_MODULE_HOOK_TIMEOUT", "15"))

__all__ = ["MODULES_DIR", "INSTALLED_DIR", "get_installed_modules", "get_module_package",
           "get_manifest", "get_all_manifests", "run_module_startup_hooks", "run_module_route_registrations",
           "get_module_graph_dirs", "resolve_module_graph_path"]

_SCAN_DIRS = [
    (MODULES_DIR, "modules"),
    (INSTALLED_DIR, "installed"),
]


def _iter_module_dirs():
    """Yield (module_dir, package_prefix) for every directory that has a manifest.json."""
    for base_dir, package in _SCAN_DIRS:
        if not base_dir.exists():
            continue
        for d in sorted(base_dir.iterdir()):
            if d.is_dir() and (d / "manifest.json").exists():
                yield d, package


def _read_modules_json() -> list[str]:
    """Read the list of installed module IDs from modules.json."""
    if not MODULES_JSON.exists():
        return []
    with open(MODULES_JSON) as f:
        data = json.load(f)
    return data.get("installed", [])


def _write_modules_json(installed: list[str]) -> None:
    """Write the list of installed module IDs to modules.json."""
    with open(MODULES_JSON, "w") as f:
        json.dump({"installed": sorted(set(installed))}, f, indent=2)
        f.write("\n")


def get_all_module_dirs() -> list[tuple[str, Path]]:
    """Return (module_id, module_dir) for every module with a manifest.json."""
    return [(d.name, d) for d, _pkg in _iter_module_dirs()]


def get_installed_modules() -> list[str]:
    """Return sorted list of active module IDs.

    Built-in modules (modules/) are active only if listed in modules.json.
    External modules (installed/) are always active if they have a manifest.json.
    """
    tracked = set(_read_modules_json())
    result = set()
    for d, package in _iter_module_dirs():
        if package == "installed" or d.name in tracked:
            result.add(d.name)
    return sorted(result)


def get_module_package(module_id: str) -> str:
    """Return the Python package prefix ('modules' or 'installed') for a module."""
    for d, package in _iter_module_dirs():
        if d.name == module_id:
            return package
    raise KeyError(f"Module '{module_id}' not found")


def install_module(name: str) -> None:
    """Add a built-in module to modules.json. External modules don't need this."""
    # External modules in installed/ are auto-detected; only track built-ins
    if (MODULES_DIR / name / "manifest.json").exists():
        installed = _read_modules_json()
        if name not in installed:
            installed.append(name)
            _write_modules_json(installed)


def uninstall_module(name: str) -> None:
    """Remove a built-in module from modules.json. External modules are removed by deleting their directory."""
    installed = _read_modules_json()
    if name in installed:
        installed.remove(name)
        _write_modules_json(installed)


def get_manifest(name: str) -> dict:
    """Load manifest.json for a given module, searching both scan directories."""
    for d, _ in _iter_module_dirs():
        if d.name == name:
            with open(d / "manifest.json") as f:
                return json.load(f)
    raise FileNotFoundError(f"No manifest found for module '{name}'")


def _run_with_timeout(fn, timeout: float):
    """Run fn() in a daemon thread, returning (ok, error_or_none).

    A timeout is reported as a TimeoutError. The thread is left running on
    timeout (Python can't kill threads); since it's a daemon, it won't block
    interpreter exit.
    """
    result: dict = {"err": None}

    def _target():
        try:
            fn()
        except BaseException as e:  # noqa: BLE001 — propagate everything
            result["err"] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, TimeoutError(f"hook exceeded {timeout:.0f}s budget")
    return result["err"] is None, result["err"]


def run_module_startup_hooks() -> None:
    """Fire on_startup() for each installed module without blocking the caller.

    Modules are pluggable — a slow or hanging hook (e.g. one waiting on an
    unreachable embedding host) must not stall server startup or any other
    module's hook. Each hook is spawned in its own daemon thread; the function
    returns immediately. Failures and timeouts are recorded asynchronously in
    the node-registry load-error map so the editor's "load error" badge
    surfaces them once they happen.
    """
    import importlib
    from src.utils.setup.logger import get_logger
    from src.utils.setup.node_registry import _LOAD_ERRORS  # noqa: PLC2701
    logger = get_logger(__name__)

    def _run_hook(mid: str, hook):
        deadline = threading.Event()

        def _watchdog():
            if not deadline.wait(_HOOK_TIMEOUT_S):
                logger.error("Module '%s' on_startup exceeded %.0fs budget", mid, _HOOK_TIMEOUT_S)
                _LOAD_ERRORS[mid] = f"on_startup exceeded {_HOOK_TIMEOUT_S:.0f}s budget"

        threading.Thread(target=_watchdog, daemon=True).start()
        try:
            hook()
        except BaseException as e:  # noqa: BLE001 — surface everything
            logger.error("Module '%s' on_startup failed: %s", mid, e)
            _LOAD_ERRORS[mid] = f"on_startup failed: {e}"
        finally:
            deadline.set()

    for mid in get_installed_modules():
        try:
            pkg = get_module_package(mid)
            mod = importlib.import_module(f"{pkg}.{mid}")
            hook = getattr(mod, "on_startup", None)
            if not callable(hook):
                continue
            threading.Thread(
                target=_run_hook, args=(mid, hook), daemon=True,
                name=f"on_startup[{mid}]",
            ).start()
        except Exception as e:
            logger.error("Module '%s' on_startup dispatch failed: %s", mid, e)
            _LOAD_ERRORS[mid] = f"on_startup dispatch failed: {e}"


def run_module_route_registrations(app) -> None:
    """Call register_routes(app) for each installed module that defines it.

    Unlike on_startup, route registration must complete before the app starts
    serving — otherwise routes added afterwards may never reach the router.
    Each call is bounded by a timeout so a misbehaving module fails fast
    instead of stalling startup, but the loop itself is synchronous.
    """
    import importlib
    from src.utils.setup.logger import get_logger
    from src.utils.setup.node_registry import _LOAD_ERRORS  # noqa: PLC2701
    logger = get_logger(__name__)

    for mid in get_installed_modules():
        try:
            pkg = get_module_package(mid)
            mod = importlib.import_module(f"{pkg}.{mid}")
            hook = getattr(mod, "register_routes", None)
            if not callable(hook):
                continue
            ok, err = _run_with_timeout(lambda h=hook: h(app), _HOOK_TIMEOUT_S)
            if not ok:
                logger.error("Module '%s' route registration failed: %s", mid, err)
                _LOAD_ERRORS[mid] = f"register_routes failed: {err}"
        except Exception as e:
            logger.error("Module '%s' route registration failed: %s", mid, e)
            _LOAD_ERRORS[mid] = f"register_routes failed: {e}"


def get_module_graph_dirs() -> list[tuple[str, Path]]:
    """Return (module_id, graphs_dir) for each installed module that has a graphs/ subdirectory."""
    result = []
    active = set(get_installed_modules())
    for d, _ in _iter_module_dirs():
        if d.name in active:
            graphs_dir = d / "graphs"
            if graphs_dir.is_dir():
                result.append((d.name, graphs_dir))
    return result


def resolve_module_graph_path(graph_id: str) -> Path | None:
    """Resolve a module@@ graph_id to an absolute file path.

    Args:
        graph_id: e.g. "module@@stackadapt/deploy.json"

    Returns:
        Resolved Path if valid, None if module or file not found.
    """
    if not graph_id.startswith("module@@"):
        return None
    rest = graph_id[len("module@@"):]
    slash = rest.find("/")
    if slash == -1:
        return None
    module_id = rest[:slash]
    rel_path = rest[slash + 1:]
    if not rel_path:
        return None

    active = set(get_installed_modules())
    if module_id not in active:
        return None

    for d, _ in _iter_module_dirs():
        if d.name == module_id:
            graphs_dir = d / "graphs"
            file_path = (graphs_dir / rel_path).resolve()
            # Security: prevent path traversal
            if not str(file_path).startswith(str(graphs_dir.resolve())):
                return None
            return file_path
    return None


def get_all_manifests() -> dict[str, dict]:
    """Return a dict of all available module manifests keyed by module ID."""
    manifests = {}
    for d, _ in _iter_module_dirs():
        with open(d / "manifest.json") as f:
            manifest = json.load(f)
        manifests[manifest["id"]] = manifest
    return manifests
