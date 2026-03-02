"""Utility functions for executing Gemini CLI tasks."""

import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from src.utils.setup.const import PROJECT_ROOT
from src.utils.exceptions import RetriableError
from src.utils.setup.logger import get_logger
from modules.llm.utils.llm_utils import parse_llm_json

logger = get_logger(__name__)


class GeminiSkill:
    """Class to interact with Gemini CLI."""

    def generate(self, prompt: str, model: Optional[str] = None) -> str:
        """Generate text using Gemini CLI."""
        cmd = ["gemini"]
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["-p", prompt])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            logger.error("Gemini generation failed: %s", e.stderr)
            raise RuntimeError(f"Gemini generation failed: {e.stderr}")


def execute_gemini(
    working_dir: Path,
    content: str,
    tools: list = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a Gemini CLI task and return the result.

    Args:
        working_dir: Path to the working directory (typically the repository directory)
        content: Content of the instructions
        tools: Optional list of tools to describe in the prompt
        model: LLM model to use for the task execution (optional, uses Gemini default if not specified)

    Returns:
        Dictionary containing the execution result (parsed from stdout)

    Raises:
        RetriableError: If JSON parsing fails (can be retried)
        RuntimeError: If skill execution fails
    """
    # Build command
    cmd = [
        "gemini",
        "--output-format",
        "text",
        '-p ""'
    ]

    # Add model if specified
    if model:
        cmd.extend(["--model", model])

    # Add approval mode
    cmd.extend(["--approval-mode", "auto_edit"])  # Auto-approve edits

    if tools:
        tool_desc = "\n".join([f"- {t.name}: {t.description}" for t in tools])
        content = f"{content}\n\nAvailable tools:\n{tool_desc}"

    logger.info("Model: %s", model if model else "default")
    logger.info("Working directory: %s", working_dir.resolve())
    logger.info("Command: %s", " ".join(cmd))

    # Ensure working_dir exists
    if not working_dir.exists():
        raise ValueError(f"Working directory does not exist: {working_dir}")

    try:
        # Execute from working_dir (repository directory) so Gemini  is in correct context
        # Pipe content as stdin
        result = subprocess.run(
            cmd,
            cwd=str(working_dir.resolve()),
            input=content,
            timeout=800,  # 10 minute timeout
            check=False,  # Don't raise on non-zero exit
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            error_output = result.stderr if result.stderr else result.stdout
            logger.error("Gemini execution failed with exit code %s: %s", result.returncode, error_output)
            raise RuntimeError(f"Gemini failed with exit code {result.returncode}: {error_output}")


        # Parse JSON from stdout (all skills need output)
        output = result.stdout.strip()
        if not output:
            raise RetriableError("No output received from Gemini. Should have output JSON.")

        result_data = parse_llm_json(output)

        return result_data
    except Exception as e:
        logger.error("Error executing Gemini: %s", str(e))
        raise RuntimeError(f"Gemini execution failed: {str(e)}") from e
