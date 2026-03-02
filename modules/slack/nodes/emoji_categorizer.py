"""Emoji Categorizer - Maps emoji reactions to a configurable output string."""

import json
from typing import Any, Dict, List

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import JSONString, Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class EmojiCategorizer(BaseNode):
    """Maps Slack emoji reactions to an output string.

    If last_slack_reply matches any emoji in the list, replaces it with
    the configured output value. Otherwise, passes through unchanged.
    """

    def __init__(
        self,
        emojis: Resolvable[JSONString] = '[":white_check_mark:", ":checkmagic:"]',
        output: Resolvable[str] = "YES",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.emojis = emojis
        self.output = output

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        reply = str(state.get("last_slack_reply", "")).strip()

        emojis_raw = self._emojis or "[]"
        if isinstance(emojis_raw, str):
            emojis_list = json.loads(emojis_raw)
        else:
            emojis_list = emojis_raw

        if reply in set(emojis_list):
            logger.info("EmojiCategorizer: Matched %s → %s", reply, self._output)
            return {"last_slack_reply": self._output}

        return {}
