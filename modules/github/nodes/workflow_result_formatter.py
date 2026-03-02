"""WorkflowResultFormatter Node - Formats workflow run results and logs into markdown."""

from typing import Any, Dict, Optional
from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


def truncate_lines(text: str, max_chars: int = 200) -> str:
    """Truncates each line in a string to a maximum number of characters."""
    if not text:
        return text
    lines = text.split("\n")
    return "\n".join([(line[:max_chars] + "..." if len(line) > max_chars else line) for line in lines])


def format_workflow_result(status: str, debug_info: Dict[str, Any], pod_logs: Optional[str] = None) -> str:
    """Formats a workflow result (status, gh logs, optional pod logs) into a readable markdown string."""
    emoji = "✅" if status == "success" else "❌"
    display_status = status

    if status == "success" and pod_logs and "--- Container:" in pod_logs:
        display_status = "failure (pod errors detected)"
        emoji = "❌"

    content = f"{emoji} *Workflow Status: {display_status.upper()}*\n\n"

    # GitHub Action logs
    gh_logs = debug_info.get("gh_action_logs", {})
    if gh_logs:
        content += "*GitHub Action Failures:*\n"
        for step, logs in gh_logs.items():
            content += f"• *Step:* {step}\n"
            content += f"```\n{truncate_lines(logs)}\n```\n"
        content += "\n"

    # Pod logs (optional, from k8s module)
    final_pod_logs = pod_logs or debug_info.get("pod_logs")
    if final_pod_logs:
        content += "*Pod Logs:*\n"
        content += f"```\n{truncate_lines(final_pod_logs)}\n```\n\n"

    # Fallback when no detailed logs available
    if status == "failure" and not gh_logs and not final_pod_logs:
        failed_steps = debug_info.get("failed_steps", [])
        if failed_steps:
            content += f"*Failed Steps:* {', '.join(failed_steps)}\n\n"
        content += "No detailed error logs could be retrieved.\n"

    return content


class WorkflowResultFormatter(BaseNode):
    """Node that formats a workflow result dict into a readable markdown string."""

    def __init__(
        self,
        workflow_result: Resolvable[Dict[str, Any]] = "{{workflow_result}}",
        pod_logs: Resolvable[Optional[str]] = "{{pod_logs}}",
        **kwargs
    ):
        super().__init__(**kwargs)
        self.workflow_result = workflow_result
        self.pod_logs = pod_logs

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        result_dict = self._workflow_result
        pod_logs = self._pod_logs

        if not result_dict or not isinstance(result_dict, dict):
            logger.info("No workflow result provided to format.")
            return {"formatted_result": ""}

        status = result_dict.get("gh_conclusion", "unknown")
        logger.info(f"Formatting workflow result (status: {status})")
        formatted = format_workflow_result(status, result_dict, pod_logs=pod_logs)

        return {"formatted_result": formatted}
