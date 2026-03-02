"""Slack Conversation History - Fetches and formats recent Slack messages."""

from typing import Any, Dict, Optional

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger
from modules.slack.utils import get_slack_history

logger = get_logger(__name__)


class SlackConversationHistory(BaseNode):
    """Node that fetches recent Slack messages and formats them as conversation history.

    Outputs ``slack_conversation_history`` as a list of ``{"role": str, "content": str}``
    dicts in chronological order, where role is ``"User"`` or ``"Assistant"``.
    """

    def __init__(
        self,
        slack_user_id: Resolvable[str] = "{{SLACK_USER_ID}}",
        fetch_until: Resolvable[Optional[str]] = "{{last_action_ts}}",
        limit: Resolvable[int] = 50,
        **kwargs,
    ):
        """
        Args:
            slack_user_id: Slack user ID or channel to fetch history from.
            fetch_until:   Only include messages newer than this timestamp.
                           Defaults to {{last_action_ts}} so history starts
                           from the last non-conversation action.
            limit:         Maximum number of messages to fetch from the API.
        """
        super().__init__(**kwargs)
        self.slack_user_id = slack_user_id
        self.fetch_until = fetch_until
        self.limit = limit

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        slack_user_id = self._slack_user_id
        if not slack_user_id:
            raise ValueError("slack_user_id is required.")

        user_id_clean = slack_user_id.lstrip("@").strip()

        fetch_until: Optional[str] = self._fetch_until
        if not fetch_until or str(fetch_until).strip() in ("", "None", "{{last_action_ts}}"):
            fetch_until = None

        limit = int(self._limit or 50)

        messages = get_slack_history(slack_user_id, limit=limit)

        if fetch_until:
            messages = [m for m in messages if float(m["ts"]) > float(fetch_until)]
            logger.info(
                "SlackConversationHistory: %d messages after ts %s for %s",
                len(messages), fetch_until, user_id_clean,
            )
        else:
            logger.info(
                "SlackConversationHistory: %d messages for %s",
                len(messages), user_id_clean,
            )

        # Messages come latest-first; reverse for chronological order
        history = []
        for msg in reversed(messages):
            text = msg.get("text", "").strip()
            if not text:
                continue
            role = "User" if msg.get("user") == user_id_clean else "Assistant"
            history.append({"role": role, "content": text})

        # Drop leading Assistant messages so history always starts with a User turn
        while history and history[0]["role"] != "User":
            history.pop(0)

        return {
            "slack_conversation_history": history,
        }
