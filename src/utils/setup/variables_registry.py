"""Global variables registry for StackFlow.

Reads and writes variables.json to store user-defined global variables
that resolve via {{KEY}} templates.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
VARIABLES_JSON = PROJECT_ROOT / "variables.json"


def _load_variables() -> dict:
    if not VARIABLES_JSON.exists():
        return {}
    with open(VARIABLES_JSON) as f:
        return json.load(f)


def _save_variables(data: dict) -> None:
    with open(VARIABLES_JSON, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def get_all_variables() -> dict:
    """Return all global variables as a dict."""
    return _load_variables()


def get_variable(key: str):
    """Return a single variable value, or None if not set."""
    return _load_variables().get(key)


def set_all_variables(variables: dict) -> None:
    """Replace all variables with the provided dict."""
    _save_variables(variables)


def delete_variable(key: str) -> bool:
    """Delete a variable. Returns True if it existed."""
    data = _load_variables()
    if key in data:
        del data[key]
        _save_variables(data)
        return True
    return False
