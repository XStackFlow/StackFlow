import subprocess
import os
from langchain_core.tools import tool
from modules.llm.tools.tool_context import resolve_path, get_repo_path

@tool
def terraform_fmt(path: str) -> str:
    """Runs 'terraform fmt' on the specified path (file or directory).
    This formats Terraform configuration files to a canonical format and style.

    Args:
        path: The file path or directory to format.
    """
    try:
        # Resolve relative paths against the repo context
        path = resolve_path(path)
        # Check if path exists
        if not os.path.exists(path):
            return f"Error: Path '{path}' not found."

        cmd = ["terraform", "fmt", path]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            cwd=str(get_repo_path())
        )

        if result.returncode == 0:
            formatted_files = result.stdout.strip()
            if formatted_files:
                return f"Successfully formatted the following files:\n{formatted_files}"
            else:
                return "Terraform files are already correctly formatted."

        return f"Terraform fmt failed:\n{result.stderr}\n{result.stdout}"
    except Exception as e:
        return f"Error running terraform fmt: {str(e)}"

# Export all Terraform tools as a list
TERRAFORM_TOOLS = [terraform_fmt]
