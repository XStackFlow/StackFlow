"""Stepper Node — Generic list iterator.

Pops items from the front of state[input_list_key] into state[output_key].
Routes "next" while items remain, "done" when the list is empty.

The list in state is mutated in-place (popped from front) and written back.
"""

from typing import Any, Dict, List

from src.nodes.abstract.router_node import RouterNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class Stepper(RouterNode):
    """Generic list iterator.

    Each cycle pops the first item from state[input_list_key] into state[output_key].
    Writes the shortened list back to state[input_list_key].
    Routes "done" when the list is empty.
    """

    def __init__(
        self,
        input_list_key: Resolvable[str] = "items",
        output_key: Resolvable[str] = "current_item",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.input_list_key = input_list_key
        self.output_key = output_key

    def get_route_options(self) -> List[str]:
        return ["next", "done"]

    def get_route(self, state: Dict[str, Any]) -> str:
        return state.get("next_step", "next")

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        list_key = self._input_list_key or "items"
        out_key = self._output_key or "current_item"

        items = list(state.get(list_key) or [])

        if not items:
            logger.info("Stepper: list empty, routing 'done'")
            return {"next_step": "done"}

        item = items.pop(0)

        logger.info("Stepper [%s]: popped item, %d remaining", list_key, len(items))

        return {
            out_key: item,
            list_key: items,
            "next_step": "next",
        }
