"""BatchCollector Node — Extends a list with all items from another list.

Takes input_list_key and output_list_key. Each run appends all items
from state[input_list_key] into state[output_list_key].
"""

from typing import Any, Dict

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class BatchCollector(BaseNode):
    """Extends output_list_key with all items from input_list_key each run."""

    def __init__(
        self,
        input_list_key: Resolvable[str] = "current_batch",
        output_list_key: Resolvable[str] = "collected_items",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.input_list_key = input_list_key
        self.output_list_key = output_list_key

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        in_key = self._input_list_key or "current_batch"
        out_key = self._output_list_key or "collected_items"

        new_items = state.get(in_key) or []
        items = list(state.get(out_key) or [])
        items.extend(new_items)

        logger.info("BatchCollector: appended %d items from %s to %s (now %d total)", len(new_items), in_key, out_key, len(items))

        return {out_key: items}
