"""FormatTemplate — renders a Jinja2 template against the current execution state."""

import math
from typing import Any, Dict

from jinja2 import Environment

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import TemplateString
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class FormatTemplate(BaseNode):
    """Renders a Jinja2 template string using values from the current state.

    Useful for composing messages, reports, or formatted strings from structured
    data in state before passing to downstream nodes (e.g. SlackNotifier).

    Example template::

        {% for pr in prs_with_unresolved_comments %}
        - {{ pr.title }} ({{ pr.unresolved_count }} comments)
        {% endfor %}
    """

    def __init__(
        self,
        template: TemplateString = "",
        output_key: str = "formatted_output",
        **kwargs,
    ):
        """
        Args:
            template:   Jinja2 template string.  Has access to all state keys.
            output_key: State key where the rendered result is stored.
        """
        super().__init__(**kwargs)
        self.template = template
        self.output_key = output_key

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        env = Environment(trim_blocks=True, lstrip_blocks=True)
        env.globals["ceil"] = math.ceil
        env.globals["sqrt"] = math.sqrt
        env.globals["int"] = int
        env.globals["min"] = min
        env.globals["max"] = max
        tpl = env.from_string(self._template)
        result = tpl.render(**state)

        logger.info("FormatTemplate: Rendered %d chars → '%s'", len(result), self._output_key)
        return {self._output_key: result}
