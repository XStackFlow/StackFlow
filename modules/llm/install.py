"""Install script for the LLM module.

Called by the StackFlow package manager when the user installs this module.
Receives env_vars collected from the UI and writes them to .env.
"""


def install(env_vars: dict) -> dict:
    """Write LLM API credentials to .env."""
    from src.utils.setup.env_utils import write_env_var

    for key, value in env_vars.items():
        if value:
            write_env_var(key, value)

    return {"success": True, "manual_steps": []}
