"""BatchedStepper Node — Generic list batch iterator.

Pops a batch of items from the front of state[input_list_key] into
state[output_list_key]. Routes "next" while items remain, "done" when empty.

The source list in state is mutated (popped from front) and written back.
"""

from typing import Any, Dict, List

from src.nodes.abstract.router_node import RouterNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class BatchedStepper(RouterNode):
    """Generic list batch iterator.

    Each cycle pops up to `size` items from state[input_list_key]
    into state[output_list_key].
    Writes the shortened list back to state[input_list_key].
    Routes "done" when the list is empty.
    """

    def __init__(
        self,
        input_list_key: Resolvable[str] = "items",
        output_list_key: Resolvable[str] = "current_batch",
        size: Resolvable[int] = 1,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.input_list_key = input_list_key
        self.output_list_key = output_list_key
        self.size = size

    def get_route_options(self) -> List[str]:
        return ["next", "done"]

    def get_route(self, state: Dict[str, Any]) -> str:
        return state.get("next_step", "next")

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        in_key = self._input_list_key or "items"
        out_key = self._output_list_key or "current_batch"
        batch_size = self._size or 1

        items = list(state.get(in_key) or [])

        if not items:
            logger.info("BatchedStepper: list empty, routing 'done'")
            return {"next_step": "done"}

        batch = items[:batch_size]
        remaining = items[batch_size:]

        logger.info("BatchedStepper [%s]: popped %d items, %d remaining", in_key, len(batch), len(remaining))

        return {
            out_key: batch,
            in_key: remaining,
            "next_step": "next",
        }
