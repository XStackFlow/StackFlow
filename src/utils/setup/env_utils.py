"""Utilities for reading and writing the project .env file."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
MODULES_DIR = PROJECT_ROOT / "modules"


def _env_path_for(module_id: str | None = None) -> Path:
    """Return the .env path for a module, or the root .env if None."""
    if module_id:
        return MODULES_DIR / module_id / ".env"
    return PROJECT_ROOT / ".env"


def read_env_file(module_id: str | None = None) -> dict[str, str]:
    """Read key=value pairs from a .env file into a dict.

    If module_id is given, reads modules/<id>/.env.
    Otherwise reads PROJECT_ROOT/.env.
    """
    env_path = _env_path_for(module_id)
    result = {}
    if not env_path.exists():
        return result
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip()
    return result


def write_env_var(key: str, value: str, module_id: str | None = None) -> None:
    """Write or update a key=value pair in a .env file.

    If module_id is given, writes to modules/<id>/.env.
    Otherwise writes to PROJECT_ROOT/.env.
    """
    env_path = _env_path_for(module_id)
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    new_lines = []
    found = False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and "=" in stripped:
            k, _, _ = stripped.partition("=")
            if k.strip() == key:
                new_lines.append(f"{key}={value}")
                found = True
                continue
        new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n")


def load_env() -> None:
    """Load root .env + all installed module .env files.

    Environment variables already set take precedence (override=False).
    """
    from dotenv import load_dotenv

    # Root .env (core config: DB, Langfuse, etc.)
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    # Module .env files (module-specific config)
    from src.utils.setup.module_registry import get_installed_modules
    modules_dir = PROJECT_ROOT / "modules"
    for module_id in get_installed_modules():
        module_env = modules_dir / module_id / ".env"
        if module_env.exists():
            load_dotenv(module_env, override=False)
