"""Node registry management for StackFlow.

This module handles the discovery and registration of all available node classes.
Core nodes (src.nodes.common) are always loaded. Module nodes are loaded based
on which modules are installed in modules.json.
"""

import inspect
import importlib
import sys
import traceback
from typing import Dict, Type

from src.utils.setup.logger import get_logger
from src.utils.setup.module_registry import get_installed_modules

logger = get_logger(__name__)

# Per-module load error tracebacks, populated by build_node_registry().
# Keys are module IDs; values are the full traceback strings.
_LOAD_ERRORS: dict[str, str] = {}

# Per-node origin metadata, populated by build_node_registry().
# Keys are node class names; values are dicts with module_id, origin, source_url.
_NODE_METADATA: dict[str, dict] = {}


def get_load_errors() -> dict[str, str]:
    """Return a copy of the current module load-error map.

    Values are full Python tracebacks for modules that failed to import.
    Only installed modules that raised an exception appear here.
    """
    return dict(_LOAD_ERRORS)


def get_node_metadata() -> dict[str, dict]:
    """Return a copy of the per-node origin metadata map.

    Each value is a dict with keys: module_id, origin ('core'|'builtin'|'external'), source_url.
    """
    return dict(_NODE_METADATA)


def build_node_registry() -> Dict[str, Type]:
    """Build the registry of available nodes and log them.

    Always loads src.nodes.common (core nodes). Additionally loads nodes from
    each installed module (modules/{name}/nodes).

    Returns:
        Dict mapping node class names to their class objects.
    """
    global _LOAD_ERRORS, _NODE_METADATA
    _LOAD_ERRORS = {}   # Reset on every build so stale errors are cleared
    _NODE_METADATA = {}

    from src.nodes.abstract.base_node import BaseNode

    def _is_node_class(obj):
        """Check if obj is a concrete BaseNode subclass (not BaseNode itself)."""
        return (inspect.isclass(obj)
                and issubclass(obj, BaseNode)
                and obj is not BaseNode
                and not inspect.isabstract(obj)
                and not obj.__name__.startswith("Base"))

    registry = {}

    # Always load core common nodes
    try:
        common = importlib.import_module("src.nodes.common")
        for name, obj in inspect.getmembers(common):
            if _is_node_class(obj):
                registry[name] = obj
                _NODE_METADATA[name] = {"module_id": "common", "origin": "core", "source_url": None}
    except Exception as e:
        logger.error("Failed to load src.nodes.common: %s", e, exc_info=True)

    # Load installed module nodes
    for module_id in get_installed_modules():
        try:
            from src.utils.setup.module_registry import get_module_package, MODULES_DIR, INSTALLED_DIR
            pkg = get_module_package(module_id)
            nodes_fqn = f"{pkg}.{module_id}.nodes"

            # Evict stale cached modules so updated files on disk are picked up.
            # This is critical after a module update — without this, Python's
            # import cache returns the old code even though files changed.
            stale_prefix = f"{pkg}.{module_id}"
            for key in [k for k in sys.modules if k == stale_prefix or k.startswith(stale_prefix + ".")]:
                del sys.modules[key]

            submodule = importlib.import_module(nodes_fqn)

            origin = "external" if pkg == "installed" else "builtin"
            source_url = None
            if origin == "external":
                url_file = INSTALLED_DIR / module_id / ".source_url"
                if url_file.exists():
                    source_url = url_file.read_text().strip()

            # Collect node classes from the nodes package and all its sub-files.
            # This ensures node files that aren't re-exported in __init__.py
            # are still discovered (common for externally authored modules).
            modules_to_scan = [submodule]
            nodes_dir = getattr(submodule, "__path__", None)
            if nodes_dir:
                import pkgutil
                for finder, sub_name, is_pkg in pkgutil.iter_modules(nodes_dir):
                    try:
                        modules_to_scan.append(importlib.import_module(f"{nodes_fqn}.{sub_name}"))
                    except Exception:
                        pass  # individual file import errors are non-fatal

            for mod in modules_to_scan:
                for name, obj in inspect.getmembers(mod):
                    if _is_node_class(obj):
                        registry[name] = obj
                        _NODE_METADATA[name] = {"module_id": module_id, "origin": origin, "source_url": source_url}
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("Failed to load module '%s': %s\n%s", module_id, e, tb)
            _LOAD_ERRORS[module_id] = tb

    # Log the registered nodes in a clean table format
    logger.info("─" * 60)
    logger.info("🚀 NODE REGISTRY INITIALIZED (%d nodes)", len(registry))
    logger.info("─" * 60)
    logger.info("  %-20s | %s", "Category", "Node Name")
    logger.info("  " + "─" * 56)

    for node_name, node_class in sorted(registry.items()):
        module_parts = node_class.__module__.split('.')
        # handles both src.nodes.common.X and modules.slack.nodes.X
        if module_parts[0] == "modules":
            category = module_parts[1]
        elif len(module_parts) > 2:
            category = module_parts[2]
        else:
            category = "other"
        logger.info("  %-20s | %s", category, node_name)
    logger.info("─" * 60)

    return registry


# Global singleton
_NODE_REGISTRY = None


def invalidate_node_registry() -> None:
    """Force the registry to be rebuilt on the next call to get_node_registry()."""
    global _NODE_REGISTRY
    _NODE_REGISTRY = None


def get_node_registry() -> Dict[str, Type]:
    """Provides a singleton registry of available nodes.

    If the registry hasn't been built yet, it scans installed modules
    and initializes it.

    Returns:
        Dict mapping node class names to their class objects.
    """
    global _NODE_REGISTRY
    if _NODE_REGISTRY is None:
        _NODE_REGISTRY = build_node_registry()
    return _NODE_REGISTRY
