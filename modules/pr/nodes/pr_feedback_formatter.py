"""PR Feedback Formatter Node - Formats PR feedback into markdown."""

from typing import Any, Dict
from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)

def format_pr_feedback(feedback: Dict[str, Any]) -> str:
    """Formats PR feedback (failed checks and comments) into a markdown string."""
    content = ""

    # Add failed checks if any
    checks = feedback.get("statusCheckRollup", []) or []
    failed_checks = [c for c in checks if c.get("conclusion") == "FAILURE"]
    if failed_checks:
        content += "### Failed GitHub Actions Checks\n\n"
        for check in failed_checks:
            if check.get("logs"):
                content += f"#### {check.get('name')}\n"
                content += f"```\n{check.get('logs')}\n```\n\n"

    # Add user comments if any
    comments = feedback.get("comments", []) or []
    if comments:
        content += "### User Review Comments\n\n"
        for comment in comments:
            author = comment.get('author', {}).get('login', 'Unknown')
            body = comment.get('body', '')
            path = comment.get('path')
            line = comment.get('line')

            location = f" (File: `{path}`, Line: {line})" if path and line else f" (File: `{path}`)" if path else ""
            content += f"- **{author}**{location}: {body}\n"
        content += "\n"

    return content

class PRFeedbackFormatter(BaseNode):
    """Node that formats PR feedback (checks and comments) into a markdown string."""

    def __init__(self, pr_feedback: Resolvable[Dict[str, Any]] = "{{pr_feedback}}", **kwargs):
        """Initialize the node."""
        super().__init__(**kwargs)
        self.pr_feedback = pr_feedback

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic."""
        feedback = self._pr_feedback
        if not feedback:
            logger.info("No PR feedback provided to format.")
            return {"formatted_pr_feedback": ""}

        logger.info("Formatting PR feedback...")
        formatted = format_pr_feedback(feedback)
        
        return {
            "formatted_pr_feedback": formatted,
        }
