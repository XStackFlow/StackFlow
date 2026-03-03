"""Graph Details Router — compares collected params against schema in code to decide routing."""

import json
from typing import List, Dict, Any, Set
from src.nodes.abstract import RouterNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


def _required_keys(example_input: Any) -> Set[str]:
    """Return all keys from the example_input schema."""
    if not example_input or example_input == "{}":
        return set()
    try:
        schema = json.loads(example_input) if isinstance(example_input, str) else example_input
        return set(schema.keys())
    except (json.JSONDecodeError, TypeError):
        return set()


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

    def __init__(
        self,
        example_input: Resolvable[str] = "{{example_input}}",
        pending_graph_params: Resolvable[str] = "{{pending_graph_params}}",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.example_input = example_input
        self.pending_graph_params = pending_graph_params

    def get_route_options(self) -> List[str]:
        return ["missing_params", "ready_to_launch"]

    def get_route(self, state: Dict[str, Any]) -> str:
        required = _required_keys(self._example_input)
        collected = _collected_keys(self._pending_graph_params)
        missing = required - collected

        if missing:
            logger.info("GraphDetailsRouter: missing params %s → missing_params", sorted(missing))
            return "missing_params"

        logger.info("GraphDetailsRouter: all params collected → ready_to_launch")
        return "ready_to_launch"

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return {"next_step": self.get_route(state)}
