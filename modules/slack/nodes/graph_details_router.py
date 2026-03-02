"""Graph Details Router — simple router that branches based on pending_next_step from the preparer LLM."""

from typing import List, Dict, Any
from src.nodes.abstract import RouterNode
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class GraphDetailsRouter(RouterNode):
    """Router node that branches based on the 'pending_next_step' key in the state
    returned by the GraphDetailsPreparer LLM.
    
    Options:
    - missing_params: Need more info from user.
    - ready_to_launch: All params collected, ready for confirmation.
    """

    def get_route_options(self) -> List[str]:
        return ["missing_params", "ready_to_launch"]

    def get_route(self, state: Dict[str, Any]) -> str:
        pending_next_step = state.get("pending_next_step")
        
        if pending_next_step in ["missing_params", "ready_to_launch"]:
            logger.info("GraphDetailsRouter: Routing to %s", pending_next_step)
            return pending_next_step
            
        logger.warning("GraphDetailsRouter: Unknown pending_next_step '%s', defaulting to missing_params", pending_next_step)
        return "missing_params"

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        route = self.get_route(state)
        return {"pending_next_step": route}
