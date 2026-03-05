"""Collector Node — Appends or replaces a value in a list in state.

Takes input_key and output_list_key. Each run appends state[input_key]
to state[output_list_key] and writes it back.

When replacement_index is a non-empty string, replaces the item at that index.
Empty string (default) means append.

Both input_key and output_list_key are state key names (not templates).
"""

from typing import Any, Dict

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class Collector(BaseNode):
    """Appends or replaces a value in output_list_key each time it runs."""

    def __init__(
        self,
        input_key: Resolvable[str] = "generated_image",
        output_list_key: Resolvable[str] = "collected_items",
        replacement_index: Resolvable[str] = "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.input_key = input_key
        self.output_list_key = output_list_key
        self.replacement_index = replacement_index

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        key = self._input_key or "generated_image"
        out = self._output_list_key or "collected_items"

        value = state.get(key)
        items = list(state.get(out) or [])

        if self._replacement_index not in (None, ""):
            idx = int(self._replacement_index)
            if 0 <= idx < len(items):
                logger.info("Collector: replacing %s[%d] from %s", out, idx, key)
                items[idx] = value
            else:
                logger.warning("Collector: replacement_index %d out of range (len=%d), appending instead", idx, len(items))
                items.append(value)
        else:
            items.append(value)
            logger.info("Collector: appended %s to %s (now %d items)", key, out, len(items))

        return {out: items}
