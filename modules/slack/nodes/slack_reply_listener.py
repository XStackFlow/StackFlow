"""Slack Reply Listener - Waits for a Slack reply or emoji reaction via a persistent Socket Mode connection."""

import time
from typing import Any, Dict, Optional

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger
from modules.slack.socket_manager import get_socket_manager

logger = get_logger(__name__)


class SlackReplyListener(BaseNode):
    """Node that waits for a Slack reply or emoji reaction.

    All instances share a single Socket Mode connection — no new socket is
    opened or closed per node execution, eliminating the lag and hang issues
    that occurred when each node managed its own connection.

    Returns either:
    - The text of a thread reply as last_slack_reply
    - The emoji name wrapped in colons (e.g. ":white_check_mark:") as last_slack_reply for reactions
    """

    def __init__(
        self,
        slack_user_id: Resolvable[str] = "{{SLACK_USER_ID}}",
        thread_ts: Resolvable[str] = "{{thread_sent_ts}}",
        timeout_minutes: Resolvable[int] = 1440,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.slack_user_id = slack_user_id
        self.thread_ts = thread_ts
        self.timeout_minutes = timeout_minutes

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        slack_user_id = self._slack_user_id
        if not slack_user_id:
            raise ValueError("slack_user_id is required.")

        user_id_clean = slack_user_id.lstrip("@").strip()

        thread_ts: Optional[str] = self._thread_ts
        if not thread_ts or thread_ts == "{{thread_sent_ts}}" or str(thread_ts).strip() in ("", "None"):
            thread_ts = None
            logger.info("SlackReplyListener: Listening for DM/channel message from %s", user_id_clean)
        else:
            logger.info("SlackReplyListener: Listening for thread reply or reaction in %s from %s", thread_ts, user_id_clean)

        timeout_seconds = int(self._timeout_minutes) * 60

        event = get_socket_manager().wait_for_reply(user_id_clean, thread_ts, timeout_seconds)

        if event is None:
            logger.warning("SlackReplyListener: Timed out after %d minutes", self._timeout_minutes)
            return {"last_slack_reply": ""}

        # Reaction event — return the emoji name wrapped in colons (e.g. ":joy:")
        # so it won't be miscategorized as plain text
        if event.get("type") == "reaction_added":
            reaction = event.get("reaction", "")
            logger.info("SlackReplyListener: Received reaction :%s:", reaction)
            return {
                "last_slack_reply": f":{reaction}:",
                "last_reply_ts": event.get("ts"),
            }

        # Text message reply
        text = event.get("text", "").strip()
        ts = event.get("ts")
        logger.info("SlackReplyListener: Received reply: %s", text)

        return {
            "last_slack_reply": text,
            "last_reply_ts": ts,
        }
