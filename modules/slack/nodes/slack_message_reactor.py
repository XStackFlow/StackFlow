"""Slack Message Reactor - Adds emoji reactions to Slack messages."""

from typing import Any, Dict

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger
from modules.slack.utils import add_slack_reaction

logger = get_logger(__name__)


class SlackMessageReactor(BaseNode):
    """Node that adds an emoji reaction to a Slack message."""

    def __init__(
        self,
        emoji: Resolvable[str] = "rocket",
        slack_user_id: Resolvable[str] = "{{SLACK_USER_ID}}",
        message_ts: Resolvable[str] = "{{last_reply_ts}}",
        **kwargs
    ):
        """Initialize the SlackMessageReactor node.

        Args:
            emoji: Emoji name to react with (e.g. 'rocket', 'eyes').
            slack_user_id: ID of the user or channel where the message is.
            message_ts: Timestamp (ts) of the message to react to.
            **kwargs: Additional properties from LiteGraph.
        """
        super().__init__(**kwargs)
        self.emoji = emoji
        self.slack_user_id = slack_user_id
        self.message_ts = message_ts

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic."""
        emoji = self._emoji
        slack_user_id = self._slack_user_id
        message_ts = self._message_ts

        if not emoji:
            raise ValueError("No emoji provided")

        if not slack_user_id:
            raise ValueError("No slack_user_id provided")

        if not message_ts or message_ts == "{{last_reply_ts}}":
            raise ValueError("No message_ts provided (last_reply_ts was not resolved)")

        logger.info("SlackMessageReactor: Adding reaction %s to message %s", emoji, message_ts)
        add_slack_reaction(slack_user_id, message_ts, emoji)

        return {"last_reacted_emoji": emoji}
