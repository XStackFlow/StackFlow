"""DynamicRouter — routes to one of N user-defined outputs based on a state value."""

import json as _json
from typing import Annotated, Any, Dict, List

from src.nodes.abstract import RouterNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)

_OTHER = "OTHER"


class DynamicRouter(RouterNode):
    """Router whose outputs are configured directly in the graph editor.

    Add this node to the graph, edit the ``route_options`` property as a JSON
    array (e.g. ``["YES", "NO"]``), and each item becomes a wirable output.
    An ``OTHER`` output is always appended automatically as a catch-all fallback.

    With ``fuzzy=False`` (default): exact case-insensitive match.
    With ``fuzzy=True``: each option is checked as a substring of the value
    (case-insensitive), e.g. value ``"please retry this"`` matches option
    ``"RETRY"``. First matching option wins; falls back to ``OTHER``.
    """

    def __init__(
        self,
        value: Resolvable[str] = "",
        route_options: Annotated[List[str], "json_type"] = [],
        fuzzy: bool = False,
        **kwargs,
    ):
        """
        Args:
            value:         The routing value to match, resolved from state (e.g. ``{{route}}``).
            route_options: JSON array of route labels, e.g. ``["YES", "NO"]``.
                           Each label becomes an output port. ``OTHER`` is always
                           added as the last output automatically.
            fuzzy:         If True, match by substring instead of exact equality.
        """
        super().__init__(**kwargs)
        self.value = value
        if isinstance(route_options, str):
            try:
                route_options = _json.loads(route_options)
            except (ValueError, TypeError):
                route_options = []
        opts = list(route_options or [])
        if _OTHER not in opts:
            opts.append(_OTHER)
        self.route_options: List[str] = opts
        self.fuzzy = fuzzy

    def get_route_options(self) -> List[str]:
        return self.route_options

    def get_route(self, state: Dict[str, Any]) -> str:
        value = str(self._value or "").strip()
        value_lower = value.lower()
        for opt in self.route_options:
            if opt == _OTHER:
                continue
            if self.fuzzy:
                if opt.lower() in value_lower:
                    return opt
            else:
                if value_lower == opt.lower():
                    return opt
        logger.info("DynamicRouter: no match for '%s', routing to OTHER", value)
        return _OTHER
