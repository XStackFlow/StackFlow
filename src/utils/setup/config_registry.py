"""Framework-level Configuration Registry — generic CRUD, encryption, and status checks.

Any module can declare a ``configurations`` block in its ``manifest.json`` to get
typed, named configuration instances with secret encryption, CRUD API endpoints,
and a generic UI — no bespoke code required.

Storage per module: ``modules/{id}/configurations.json``
Encryption key:     ``modules/{id}/.config_secret_key``

Manifest schema (under ``setup``)::

    "configurations": {
        "label": "Providers",
        "types": {
            "openai": {
                "label": "OpenAI",
                "description": "...",
                "options": [
                    {"key": "API_KEY", "label": "API Key", "secret": true, ...}
                ],
                "status_check": {"type": "http", "url_template": "{BASE_URL}/", "auth_header": "Bearer {API_KEY}"}
            }
        }
    }
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
MODULES_DIR = PROJECT_ROOT / "modules"

# Sentinel returned by the API for secret fields that are set.
# The browser never receives the actual value.
SECRET_SET_SENTINEL = "__SET__"

_FERNET_PREFIX = "fernet:"


# ── Path helpers ────────────────────────────────────────────────────────────

def _module_dir(module_id: str) -> Path:
    """Resolve the actual directory for a module (modules/ or installed/)."""
    from src.utils.setup.module_registry import INSTALLED_DIR
    installed_path = INSTALLED_DIR / module_id
    if installed_path.exists():
        return installed_path
    return MODULES_DIR / module_id


def _configs_file(module_id: str) -> Path:
    return _module_dir(module_id) / "configurations.json"


def _secret_key_file(module_id: str) -> Path:
    return _module_dir(module_id) / ".config_secret_key"


# ── Manifest helpers ────────────────────────────────────────────────────────

def _get_configurations_schema(module_id: str) -> dict:
    """Return the full ``setup.configurations`` block from the manifest, or {}."""
    from src.utils.setup.module_registry import get_manifest
    manifest = get_manifest(module_id)
    return manifest.get("setup", {}).get("configurations", {})


def get_config_types(module_id: str) -> dict:
    """Return ``configurations.types`` from the manifest, or {}."""
    return _get_configurations_schema(module_id).get("types", {})


def get_config_label(module_id: str) -> str:
    """Return ``configurations.label`` from the manifest, or 'Configurations'."""
    return _get_configurations_schema(module_id).get("label", "Configurations")


# ── Encryption helpers ──────────────────────────────────────────────────────

def _get_fernet(module_id: str):
    """Return a Fernet instance for a module, creating the key file if needed."""
    from cryptography.fernet import Fernet

    key_file = _secret_key_file(module_id)
    if not key_file.exists():
        key = Fernet.generate_key()
        key_file.write_bytes(key)
        try:
            key_file.chmod(0o600)
        except OSError:
            pass

    key = key_file.read_bytes().strip()
    return Fernet(key)


def _encrypt(module_id: str, value: str) -> str:
    token = _get_fernet(module_id).encrypt(value.encode())
    return _FERNET_PREFIX + token.decode()


def _decrypt(module_id: str, value: str) -> str:
    token = value[len(_FERNET_PREFIX):].encode()
    return _get_fernet(module_id).decrypt(token).decode()


def _is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_FERNET_PREFIX)


def _secret_keys_for_type(module_id: str, config_type: str) -> set[str]:
    """Return option keys marked ``secret`` for a config type in this module."""
    types = get_config_types(module_id)
    type_meta = types.get(config_type, {})
    return {opt["key"] for opt in type_meta.get("options", []) if opt.get("secret")}


def _encrypt_item_secrets(module_id: str, item: dict) -> dict:
    secret_keys = _secret_keys_for_type(module_id, item.get("type", ""))
    if not secret_keys:
        return item
    opts = dict(item.get("options") or {})
    for key in secret_keys:
        val = opts.get(key, "")
        if val and not _is_encrypted(val):
            opts[key] = _encrypt(module_id, val)
    return {**item, "options": opts}


def _decrypt_item_secrets(module_id: str, item: dict) -> dict:
    secret_keys = _secret_keys_for_type(module_id, item.get("type", ""))
    if not secret_keys:
        return item
    opts = dict(item.get("options") or {})
    for key in secret_keys:
        val = opts.get(key, "")
        if val and _is_encrypted(val):
            try:
                opts[key] = _decrypt(module_id, val)
            except Exception:
                opts[key] = ""  # corrupt/unreadable — treat as unset
    return {**item, "options": opts}


def _mask_item_secrets(module_id: str, item: dict) -> dict:
    secret_keys = _secret_keys_for_type(module_id, item.get("type", ""))
    if not secret_keys:
        return item
    opts = dict(item.get("options") or {})
    for key in secret_keys:
        val = opts.get(key, "")
        opts[key] = SECRET_SET_SENTINEL if val else ""
    return {**item, "options": opts}


# ── File I/O (with auto-migration from legacy providers.json) ──────────────

def _maybe_migrate_legacy(module_id: str) -> None:
    """One-time migration: rename providers.json → configurations.json and key file."""
    configs = _configs_file(module_id)
    if configs.exists():
        return

    # Migrate providers.json → configurations.json
    legacy_data_file = MODULES_DIR / module_id / "providers.json"
    if legacy_data_file.exists():
        with open(legacy_data_file) as f:
            legacy = json.load(f)
        migrated = {"items": legacy.get("providers", [])}
        with open(configs, "w") as f:
            json.dump(migrated, f, indent=2)
            f.write("\n")
        legacy_data_file.rename(legacy_data_file.with_suffix(".json.bak"))

    # Migrate .provider_secret_key → .config_secret_key
    new_key = _secret_key_file(module_id)
    if not new_key.exists():
        old_key = MODULES_DIR / module_id / ".provider_secret_key"
        if old_key.exists():
            old_key.rename(new_key)


def _load(module_id: str) -> dict:
    """Load configurations.json, decrypting any secret fields."""
    _maybe_migrate_legacy(module_id)
    configs = _configs_file(module_id)
    if not configs.exists():
        return {"items": []}
    with open(configs) as f:
        data = json.load(f)
    data["items"] = [_decrypt_item_secrets(module_id, item) for item in data.get("items", [])]
    return data


def _save(module_id: str, data: dict) -> None:
    """Encrypt secret fields and write configurations.json."""
    to_save = {
        **data,
        "items": [_encrypt_item_secrets(module_id, item) for item in data.get("items", [])],
    }
    with open(_configs_file(module_id), "w") as f:
        json.dump(to_save, f, indent=2)
        f.write("\n")


# ── CRUD (public API) ──────────────────────────────────────────────────────

def get_configurations(module_id: str) -> list[dict]:
    """Return all items with secrets decrypted (internal use only)."""
    return _load(module_id).get("items", [])


def get_configurations_masked(module_id: str) -> list[dict]:
    """Return items with secret values replaced by sentinel (safe for API responses)."""
    return [_mask_item_secrets(module_id, item) for item in get_configurations(module_id)]


def set_configurations(module_id: str, items: list[dict]) -> None:
    """Replace the entire configuration list (encrypts secrets before persisting)."""
    _save(module_id, {"items": items})


def get_configuration(module_id: str, name: str) -> Optional[dict]:
    """Look up a single configuration by name. Returns None if not found."""
    for item in get_configurations(module_id):
        if item.get("name") == name:
            return item
    return None


def get_configuration_type(module_id: str, name: str) -> Optional[str]:
    """Return the type string for a named configuration, or None."""
    item = get_configuration(module_id, name)
    return item["type"] if item else None


# ── Status checks (declarative, driven by manifest) ────────────────────────

def _interpolate_template(template: str, options: dict, type_schema: dict) -> str:
    """Replace ``{KEY}`` placeholders with option values, falling back to placeholder defaults."""
    placeholders = {opt["key"]: opt.get("placeholder", "") for opt in type_schema.get("options", [])}

    def replacer(match: re.Match) -> str:
        key = match.group(1)
        return options.get(key) or placeholders.get(key, "")

    return re.sub(r"\{(\w+)\}", replacer, template)


def _check_cli_status(command: str) -> dict:
    """Check whether a CLI tool is installed and responsive."""
    check_cmd = f"{command} --version"
    found = shutil.which(command) is not None
    if not found:
        return {
            "available": False,
            "command": check_cmd,
            "message": f"'{command}' not found in PATH",
        }
    try:
        result = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = (result.stdout or result.stderr or "").strip()
        return {
            "available": True,
            "command": check_cmd,
            "message": output[:200] if output else "OK",
        }
    except subprocess.TimeoutExpired:
        return {"available": True, "command": check_cmd, "message": "installed (version check timed out)"}
    except Exception as e:
        return {"available": True, "command": check_cmd, "message": f"installed (version check failed: {e})"}


def _check_http_status(url: str, auth_header: Optional[str] = None) -> dict:
    """Make an HTTP GET request to check endpoint connectivity."""
    import urllib.error
    import urllib.request

    # Build display command (mask auth value)
    if auth_header:
        parts = auth_header.split(" ", 1)
        if len(parts) == 2 and len(parts[1]) > 6:
            masked_header = f"{parts[0]} {parts[1][:6]}..."
        else:
            masked_header = auth_header[:10] + "..."
        curl_cmd = f'curl -s -H "Authorization: {masked_header}" {url}'
    else:
        curl_cmd = f"curl -s {url}"

    # Build actual request
    headers: dict[str, str] = {"Accept": "application/json"}
    if auth_header:
        headers["Authorization"] = auth_header

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode(errors="replace")
            snippet = body.strip()[:120].replace("\n", " ")
            return {
                "available": True,
                "command": curl_cmd,
                "message": f"HTTP {resp.status} — {snippet}",
            }
    except urllib.error.HTTPError as e:
        return {
            "available": False,
            "command": curl_cmd,
            "message": f"HTTP {e.code}: {e.reason}",
        }
    except OSError as e:
        return {
            "available": False,
            "command": curl_cmd,
            "message": str(e),
        }
    except Exception as e:
        return {
            "available": False,
            "command": curl_cmd,
            "message": str(e)[:200],
        }


def check_configuration_status(module_id: str, config: dict) -> dict:
    """Run the declarative status check for a configuration item.

    Reads ``status_check`` from the type schema in the manifest and dispatches:
      - ``{"type": "command", "command": "..."}`` → CLI version check
      - ``{"type": "http", "url_template": "...", "auth_header": "..."}`` → HTTP GET

    Returns ``{"available": bool, "command": str|None, "message": str}``
    """
    config_type = config.get("type", "")
    types = get_config_types(module_id)
    type_schema = types.get(config_type, {})
    status_check = type_schema.get("status_check")

    if not status_check:
        return {"available": True, "command": None, "message": "OK"}

    opts = config.get("options") or {}
    check_type = status_check.get("type", "")

    if check_type == "command":
        cmd = status_check.get("command", "")
        # Check required options first — if a required option is missing, skip CLI check
        for opt in type_schema.get("options", []):
            if opt.get("required") and not opts.get(opt["key"], "").strip():
                return {"available": False, "command": None, "message": f"{opt['label']} not set"}
        return _check_cli_status(cmd)

    if check_type == "http":
        url_template = status_check.get("url_template", "")
        url = _interpolate_template(url_template, opts, type_schema).rstrip("/") + "/"
        # Strip trailing double slash edge case
        url = re.sub(r"//+$", "/", url)

        auth_template = status_check.get("auth_header")
        auth_header = None
        if auth_template:
            auth_header = _interpolate_template(auth_template, opts, type_schema)
            # If the auth value is empty after interpolation, skip it
            if not auth_header.strip() or auth_header == auth_template:
                auth_header = None

        return _check_http_status(url, auth_header=auth_header)

    return {"available": True, "command": None, "message": "OK"}
