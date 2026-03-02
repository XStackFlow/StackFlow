"""Slack Assistant Router Node - Routes based on the assistant's decided next step."""

from typing import List, Dict, Any
from src.nodes.abstract import RouterNode
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class SlackAssistantRouter(RouterNode):
    """Router node that branches based on the 'pending_next_step' key in the state.
    
    Options:
    - run_graph: User wants to run a graph (goes to param collection)
    - conversation: General conversation
    """

    def get_route_options(self) -> List[str]:
        """Return all possible routing options."""
        return ["run_graph", "new_session", "conversation"]

    def get_route(self, state: Dict[str, Any]) -> str:
        """Return the chosen route based on 'pending_next_step'."""
        pending_next_step = state.get("pending_next_step")
        
        if pending_next_step == "run_graph":
            logger.info("SlackAssistantRouter: Routing to run_graph")
            return "run_graph"
            
        if pending_next_step == "new_session":
            logger.info("SlackAssistantRouter: Routing to new_session")
            return "new_session"
            
        if pending_next_step == "conversation":
            logger.info("SlackAssistantRouter: Routing to conversation")
            return "conversation"
            
        logger.warning("SlackAssistantRouter: Unknown or missing pending_next_step '%s', defaulting to conversation", pending_next_step)
        return "conversation"

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the routing logic."""
        route = self.get_route(state)
        result = {"pending_next_step": route}
        
        return result

