"""Install script for the Jira module.

The Jira module has no env vars — credentials are managed by the Jira CLI.
Returns the interactive manual step so the UI can surface it to the user.
"""


def install(env_vars: dict) -> dict:
    """No env vars to write; remind user to run `jira init`."""
    return {
        "success": True,
        "manual_steps": [
            {
                "type": "run_command",
                "command": "jira init",
                "message": "Configuring Jira CLI credentials (URL, username, API token)...",
                "interactive": True,
            }
        ],
    }
