"""Deployment Router Node - Routes based on deployment feedback."""

from typing import List, Dict, Any
from src.nodes.abstract import RouterNode
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class SlackMessageRouterDeploymentVerification(RouterNode):
    """Router node that branches based on the 'last_slack_reply' key in the state.
    
    Options:
    - RETRY: If message contains 'RETRY'
    - LGTM: If message contains 'LGTM'
    - OTHER: Default fallback
    """

    def get_route_options(self) -> List[str]:
        """Return all possible routing options."""
        return ["RETRY", "LGTM", "OTHER"]

    def get_route(self, state: Dict[str, Any]) -> str:
        """Return the chosen route based on last Slack reply (normalized to upper)."""
        message = str(state.get("last_slack_reply", "")).upper()
        
        if not message:
            logger.info("SlackMessageRouterDeploymentVerification: No reply found in state, defaulting to OTHER")
            return "OTHER"
            
        if "RETRY" in message:
            logger.info("SlackMessageRouterDeploymentVerification: Detected 'RETRY' in message")
            return "RETRY"
            
        if "LGTM" in message:
            logger.info("SlackMessageRouterDeploymentVerification: Detected 'LGTM' in message")
            return "LGTM"
            
        logger.info("SlackMessageRouterDeploymentVerification: No keyword found, defaulting to OTHER")
        return "OTHER"

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Override _run to both route and normalize the reply to uppercase in state."""
        route = self.get_route(state)
        return {
            "next_step": route,
            "last_slack_reply": str(state.get("last_slack_reply", "")).upper()
        }
