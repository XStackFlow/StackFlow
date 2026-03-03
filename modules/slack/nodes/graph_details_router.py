"""Graph Details Router — compares collected params against schema in code to decide routing."""

import json
import re
from typing import List, Dict, Any, Set
from src.nodes.abstract import RouterNode
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)

_SYSTEM_VALUE_RE = re.compile(r"\{\{.+?\}\}")


def _required_keys(example_input: Any) -> Set[str]:
    """Return schema keys that require user input (exclude system-provided {{...}} values)."""
    if not example_input or example_input == "{}":
        return set()
    try:
        schema = json.loads(example_input) if isinstance(example_input, str) else example_input
    except (json.JSONDecodeError, TypeError):
        return set()
    return {
        key for key, value in schema.items()
        if not _SYSTEM_VALUE_RE.search(json.dumps(value))
    }


def _collected_keys(pending_graph_params: Any) -> Set[str]:
    """Return keys present in the collected params."""
    if not pending_graph_params:
        return set()
    try:
        params = json.loads(pending_graph_params) if isinstance(pending_graph_params, str) else pending_graph_params
        return set(params.keys())
    except (json.JSONDecodeError, TypeError):
        return set()


class GraphDetailsRouter(RouterNode):
    """Router that checks whether all required graph params have been collected.

    Reads `example_input` (the graph's schema) and `pending_graph_params`
    (what the LLM has extracted so far), compares them in code, and routes:
    - ready_to_launch: all required keys are present
    - missing_params:  one or more required keys are still missing
    """

    def get_route_options(self) -> List[str]:
        return ["missing_params", "ready_to_launch"]

    def get_route(self, state: Dict[str, Any]) -> str:
        required = _required_keys(state.get("example_input", "{}"))
        collected = _collected_keys(state.get("pending_graph_params"))
        missing = required - collected

        if missing:
            logger.info("GraphDetailsRouter: missing params %s → missing_params", sorted(missing))
            return "missing_params"

        logger.info("GraphDetailsRouter: all params collected → ready_to_launch")
        return "ready_to_launch"

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        route = self.get_route(state)
        result: Dict[str, Any] = {"pending_next_step": route}

        # Provide a fallback reply if the LLM didn't generate one but params are still missing
        if route == "missing_params" and not state.get("reply"):
            required = _required_keys(state.get("example_input", "{}"))
            collected = _collected_keys(state.get("pending_graph_params"))
            missing = sorted(required - collected)
            result["reply"] = f"Please provide the following: {', '.join(missing)}"
            logger.info("GraphDetailsRouter: generated fallback reply for %s", missing)

        return result
