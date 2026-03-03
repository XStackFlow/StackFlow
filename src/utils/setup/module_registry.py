"""Module registry for StackFlow package manager.

Discovers modules by scanning for manifest.json files in two directories:
  - modules/   built-in modules shipped with the repo (tracked by git)
  - installed/ externally installed modules (gitignored)

No external state file is needed — a module is available if its directory exists.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
MODULES_DIR = PROJECT_ROOT / "modules"
INSTALLED_DIR = PROJECT_ROOT / "installed"

__all__ = ["MODULES_DIR", "INSTALLED_DIR", "get_installed_modules", "get_module_package",
           "get_manifest", "get_all_manifests", "run_module_startup_hooks", "run_module_route_registrations"]

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


def get_installed_modules() -> list[str]:
    """Return sorted list of all module IDs across both scan directories."""
    return sorted(d.name for d, _ in _iter_module_dirs())


def get_module_package(module_id: str) -> str:
    """Return the Python package prefix ('modules' or 'installed') for a module."""
    for d, package in _iter_module_dirs():
        if d.name == module_id:
            return package
    raise KeyError(f"Module '{module_id}' not found")


def install_module(name: str) -> None:
    """No-op: module is considered installed as long as its directory exists."""
    pass


def uninstall_module(name: str) -> None:
    """No-op: remove the module directory to uninstall."""
    pass


def get_manifest(name: str) -> dict:
    """Load manifest.json for a given module, searching both scan directories."""
    for d, _ in _iter_module_dirs():
        if d.name == name:
            with open(d / "manifest.json") as f:
                return json.load(f)
    raise FileNotFoundError(f"No manifest found for module '{name}'")


def run_module_startup_hooks() -> None:
    """Call on_startup() for each installed module that defines it."""
    import importlib
    from src.utils.setup.logger import get_logger
    logger = get_logger(__name__)

    for mid in get_installed_modules():
        try:
            pkg = get_module_package(mid)
            mod = importlib.import_module(f"{pkg}.{mid}")
            if callable(getattr(mod, "on_startup", None)):
                mod.on_startup()
        except Exception as e:
            logger.error("Module '%s' startup hook failed: %s", mid, e)


def run_module_route_registrations(app) -> None:
    """Call register_routes(app) for each installed module that defines it."""
    import importlib
    from src.utils.setup.logger import get_logger
    logger = get_logger(__name__)

    for mid in get_installed_modules():
        try:
            pkg = get_module_package(mid)
            mod = importlib.import_module(f"{pkg}.{mid}")
            if callable(getattr(mod, "register_routes", None)):
                mod.register_routes(app)
        except Exception as e:
            logger.error("Module '%s' route registration failed: %s", mid, e)


def get_all_manifests() -> dict[str, dict]:
    """Return a dict of all available module manifests keyed by module ID."""
    manifests = {}
    for d, _ in _iter_module_dirs():
        with open(d / "manifest.json") as f:
            manifest = json.load(f)
        manifests[manifest["id"]] = manifest
    return manifests
