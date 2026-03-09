"""Template manager for resolving domain-specific templates using state and environment variables."""

import os
import re
from datetime import datetime
from typing import Any, Dict, Optional
from src.utils.setup.logger import get_logger
from src.utils.setup.variables_registry import get_variable as _get_global_variable

logger = get_logger(__name__)


def get_value_by_path(data: Any, path: str, root: Any = None) -> Any:
    """Resolves a dot-notated path with optional bracket indexing.

    Supports:
    - "a.b.c"          → data["a"]["b"]["c"]
    - "a[0]"           → data["a"][0]
    - "a[b.index]"     → data["a"][ root["b"]["index"] ]  (dynamic index from state)

    Args:
        data:  The root object to traverse (usually state).
        path:  Dot/bracket path string.
        root:  The top-level state used to resolve dynamic bracket expressions.
               Defaults to *data* when not supplied.
    """
    if root is None:
        root = data

    # Tokenize: split on '.' but also extract bracket groups
    # e.g. "character_bible[cur_feedback.index].name"
    #  → ["character_bible", "[cur_feedback.index]", "name"]
    tokens = re.findall(r'\[([^\]]*)\]|([^.\[\]]+)', path)

    current = data
    for bracket_expr, dot_key in tokens:
        if dot_key:
            # Regular dict key
            if isinstance(current, dict):
                current = current.get(dot_key)
            else:
                return None
        elif bracket_expr is not None:
            # Bracket access — resolve index
            expr = bracket_expr.strip()
            # Try literal int first
            try:
                idx = int(expr)
            except ValueError:
                # Dynamic: resolve the expression as a path in root state
                idx = get_value_by_path(root, expr, root)
                if idx is None:
                    return None
                try:
                    idx = int(idx)
                except (ValueError, TypeError):
                    # Not an int — try as dict key
                    if isinstance(current, dict):
                        current = current.get(str(idx))
                        continue
                    return None
            # Index into list or dict
            if isinstance(current, list):
                if 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            elif isinstance(current, dict):
                # Try int key first, then string — checkpoint serialization
                # may convert int keys to strings or vice versa.
                current = current.get(idx) if idx in current else current.get(str(idx))
            else:
                return None
        if current is None:
            return None
    return current


def resolve_templates(data: Any, state: Dict[str, Any]) -> Any:
    """Recursively resolves templates in a data structure (dict, list, or str)."""
    if isinstance(data, str):
        return render_template(data, state)
    elif isinstance(data, dict):
        return {render_template(k, state) if isinstance(k, str) and '{{' in k else k: resolve_templates(v, state) for k, v in data.items()}
    elif isinstance(data, list):
        return [resolve_templates(item, state) for item in data]
    return data


def render_template(template: Any, state: Dict[str, Any]) -> Any:
    """Renders a template or data structure by interpolating state variables.

    If input is a string, returns interpreted string.
    If input is a dict or list, recursively resolves all strings within it.

    Supports:
    - {{variable}}: Resolves 'variable' from the top-level state.
    - {{state.nested.key}}: Resolves nested keys from state.
    - {{timestamp}}: Current datetime in YYYYMMDD-HHMMSS format.
    - {{ENV_VAR}}: Resolves environment variables.
    """
    if not isinstance(template, str):
        return resolve_templates(template, state)

    if isinstance(template, str):
        # Optimized path for direct variable mapping: "{{variable}}"
        # If it's JUST a template tag, return the raw object if found.
        match = re.fullmatch(r"\{\{(.*?)\}\}", template.strip())
        if match:
            path = match.group(1).strip()
            # Skip transformations or special variables for raw mapping for now
            # (only support simple paths like "{{deployment_status}}" or "{{state.job}}")
            if not re.search(r"\.(replace|strip|lower|upper)\(", path) and path != "timestamp":
                # Handle len() wrapper — e.g. {{len(cur_keyframes)}}
                len_match = re.fullmatch(r"len\((.+)\)", path)
                if len_match:
                    inner_path = len_match.group(1).strip()
                    inner_path = inner_path[6:] if inner_path.startswith("state.") else inner_path
                    val = get_value_by_path(state, inner_path)
                    if val is not None and hasattr(val, '__len__'):
                        return len(val)
                    return 0

                # Check global variables first
                gval = _get_global_variable(path)
                if gval is not None:
                    return gval
                # Then state
                search_path = path[6:] if path.startswith("state.") else path
                val = get_value_by_path(state, search_path)
                if val is not None:
                    return val

    if not template:
        return ""

    def replace(match):
        path = match.group(1).strip()

        # Handle len() wrapper — e.g. {{len(cur_keyframes)}}
        len_match = re.fullmatch(r"len\((.+)\)", path)
        if len_match:
            inner_path = len_match.group(1).strip()
            inner_path = inner_path[6:] if inner_path.startswith("state.") else inner_path
            val = get_value_by_path(state, inner_path)
            if val is not None and hasattr(val, '__len__'):
                return str(len(val))
            return "0"

        # Check for transformations (e.g., .replace("-", "_"))
        transform_match = re.search(r"\.replace\((['\"])(.*?)\1,\s*(['\"])(.*?)\3\)$", path)
        transformation = None
        if transform_match:
            old_val = transform_match.group(2)
            new_val = transform_match.group(4)
            transformation = ("replace", old_val, new_val)
            path = path[:transform_match.start()]

        # 1. Check for special context variables
        if path == "timestamp":
            val = datetime.now().strftime("%Y%m%d-%H%M%S")
        # 2. Check in global variables (user-defined, from variables.json)
        elif _get_global_variable(path) is not None:
            val = _get_global_variable(path)
        # 3. Check in environment
        elif os.getenv(path) is not None:
            val = os.getenv(path)
        # 4. Check in state
        else:
            search_path = path[6:] if path.startswith("state.") else path
            top_key = re.split(r'[.\[]', search_path)[0]
            if top_key not in state:
                logger.warning("Template variable '{{%s}}' not found in state or environment. Resolving to empty string.", path)
                return ""
            val = get_value_by_path(state, search_path)

        if val is not None:
            val_str = str(val)
            if transformation and transformation[0] == "replace":
                val_str = val_str.replace(transformation[1], transformation[2])
            return val_str

        return ""

    return re.sub(r"\{\{(.*?)\}\}", replace, template)
