"""PR Stage Getter - Parses pr_feedback and writes the current stage to pr_state."""

from typing import Any, Dict
from src.nodes.abstract import BaseNode
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class PRStageGetter(BaseNode):
    """Parses pr_feedback and writes the derived stage string to pr_state.

    Stage values:
        pr_merged           — PR has been merged
        pr_approved         — PR has been approved, waiting for merge
        pr_in_progress      — CI checks are still running
        pr_has_issue        — Failed checks or unresolved comments
        pr_pending_review   — No notable condition yet, still waiting
    """

    def _get_stage(self, feedback: Dict[str, Any]) -> str:
        """Derive the current PR stage from pr_feedback."""
        if feedback.get("state") == "MERGED":
            return "pr_merged"

        if feedback.get("reviewDecision") == "APPROVED":
            return "pr_approved"

        checks = feedback.get("statusCheckRollup", [])
        if any(c.get("status") == "IN_PROGRESS" for c in checks):
            return "pr_in_progress"

        failed_checks = [c for c in checks if c.get("conclusion") == "FAILURE"]
        if failed_checks or feedback.get("comments", []):
            return "pr_has_issue"

        return "pr_pending_review"

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        feedback = state.get("pr_feedback")
        if not feedback:
            raise ValueError("pr_feedback is required in state")

        stage = self._get_stage(feedback)
        logger.info("PR stage: %s", stage)

        return {"pr_state": stage}
