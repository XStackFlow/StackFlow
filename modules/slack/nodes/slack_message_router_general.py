"""General Slack Message Router — simple keyword-based YES/NO/OTHER router."""

from typing import List, Dict, Any
from src.nodes.abstract import RouterNode
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class SlackMessageRouterGeneral(RouterNode):
    """Router node that checks the last_slack_reply for YES, NO, or OTHER.

    Routes:
    - YES: If message contains 'yes', 'y', 'confirm', 'go', 'ok', 'sure', 'do it'
    - NO: If message contains 'no', 'n', 'cancel', 'stop', 'abort', 'nah'
    - OTHER: Default fallback
    """

    YES_KEYWORDS = {"yes", "y", "confirm", "go", "ok", "sure", "do it", "yep", "yeah", "approved", "lgtm"}
    NO_KEYWORDS = {"no", "n", "cancel", "stop", "abort", "nah", "nope", "deny", "reject"}

    def get_route_options(self) -> List[str]:
        return ["YES", "NO", "OTHER"]

    def get_route(self, state: Dict[str, Any]) -> str:
        message = str(state.get("last_slack_reply", "")).strip().lower()

        if not message:
            logger.info("SlackMessageRouterGeneral: No reply, defaulting to OTHER")
            return "OTHER"

        # Check for YES keywords
        for keyword in self.YES_KEYWORDS:
            if keyword in message:
                logger.info("SlackMessageRouterGeneral: Detected YES ('%s')", keyword)
                return "YES"

        # Check for NO keywords
        for keyword in self.NO_KEYWORDS:
            if keyword in message:
                logger.info("SlackMessageRouterGeneral: Detected NO ('%s')", keyword)
                return "NO"

        logger.info("SlackMessageRouterGeneral: No keyword match, defaulting to OTHER")
        return "OTHER"

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        route = self.get_route(state)
        return {"next_step": route}
