"""Module registry for StackFlow package manager.

Reads and writes modules.json to track which modules are installed,
and reads manifest.json files from the modules/ directory.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
MODULES_JSON = PROJECT_ROOT / "modules.json"
MODULES_DIR = PROJECT_ROOT / "modules"


def _load_modules_json() -> dict:
    if not MODULES_JSON.exists():
        return {"installed": []}
    with open(MODULES_JSON) as f:
        return json.load(f)


def _save_modules_json(data: dict) -> None:
    with open(MODULES_JSON, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def get_installed_modules() -> list[str]:
    """Return list of installed module IDs."""
    return _load_modules_json().get("installed", [])


def install_module(name: str) -> None:
    """Mark a module as installed in modules.json."""
    data = _load_modules_json()
    if name not in data["installed"]:
        data["installed"].append(name)
        _save_modules_json(data)


def uninstall_module(name: str) -> None:
    """Remove a module from modules.json."""
    data = _load_modules_json()
    data["installed"] = [m for m in data["installed"] if m != name]
    _save_modules_json(data)


def get_manifest(name: str) -> dict:
    """Load manifest.json for a given module."""
    manifest_path = MODULES_DIR / name / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest found for module '{name}'")
    with open(manifest_path) as f:
        return json.load(f)


def run_module_startup_hooks() -> None:
    """Call on_startup() for each installed module that defines it.

    Modules opt in by defining an on_startup() function in their top-level __init__.py.
    Failures are logged but do not prevent other modules or the server from starting.
    """
    import importlib
    from src.utils.setup.logger import get_logger
    logger = get_logger(__name__)

    for mid in get_installed_modules():
        try:
            mod = importlib.import_module(f"modules.{mid}")
            if callable(getattr(mod, "on_startup", None)):
                mod.on_startup()
        except Exception as e:
            logger.error("Module '%s' startup hook failed: %s", mid, e)


def run_module_route_registrations(app) -> None:
    """Call register_routes(app) for each installed module that defines it.

    Modules opt in by defining a register_routes(app) function in their top-level __init__.py.
    This allows modules to add FastAPI routes without src/ importing from modules/ directly.
    Failures are logged but do not prevent other modules or the server from starting.
    """
    import importlib
    from src.utils.setup.logger import get_logger
    logger = get_logger(__name__)

    for mid in get_installed_modules():
        try:
            mod = importlib.import_module(f"modules.{mid}")
            if callable(getattr(mod, "register_routes", None)):
                mod.register_routes(app)
        except Exception as e:
            logger.error("Module '%s' route registration failed: %s", mid, e)


def get_all_manifests() -> dict[str, dict]:
    """Return a dict of all available module manifests keyed by module ID."""
    manifests = {}
    if not MODULES_DIR.exists():
        return manifests
    for module_dir in sorted(MODULES_DIR.iterdir()):
        manifest_path = module_dir / "manifest.json"
        if module_dir.is_dir() and manifest_path.exists():
            with open(manifest_path) as f:
                manifest = json.load(f)
            manifests[manifest["id"]] = manifest
    return manifests
