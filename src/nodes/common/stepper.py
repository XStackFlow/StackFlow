"""Stepper Node — Generic list iterator.

Pops items from the front of state[input_list_key] into state[output_key].
Routes "next" while items remain, "done" when the list is empty.

The list in state is mutated in-place (popped from front) and written back.

Supports dot-notation for nested keys (e.g. "tmp.items" reads/writes state["tmp"]["items"]).
"""

from typing import Any, Dict, List

from src.nodes.abstract.router_node import RouterNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


def _deep_get(d, dotkey):
    """Get a value from a nested dict using dot notation."""
    keys = dotkey.split(".")
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _deep_set(result, dotkey, value):
    """Build a nested dict update for dot notation.

    Returns a dict suitable for merging into state via langgraph's reducer.
    E.g. _deep_set({}, "a.b", 1) -> {"a": {"b": 1}}
    """
    keys = dotkey.split(".")
    current = result
    for k in keys[:-1]:
        if k not in current or not isinstance(current[k], dict):
            current[k] = {}
        current = current[k]
    current[keys[-1]] = value
    return result


class Stepper(RouterNode):
    """Generic list iterator.

    Each cycle pops the first item from state[input_list_key] into state[output_key].
    Writes the shortened list back to state[input_list_key].
    Routes "done" when the list is empty.

    Supports dot-notation for nested keys (e.g. "tmp.items" -> state["tmp"]["items"]).
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

        items = list(_deep_get(state, list_key) or [])

        if not items:
            logger.info("Stepper: list empty, routing 'done'")
            return {"next_step": "done"}

        item = items.pop(0)

        logger.info("Stepper [%s]: popped item, %d remaining", list_key, len(items))

        result = {"next_step": "next"}
        _deep_set(result, out_key, item)
        _deep_set(result, list_key, items)
        return result
