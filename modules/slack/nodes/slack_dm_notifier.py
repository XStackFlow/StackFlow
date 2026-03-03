"""Slack DM Notifier - Sends Slack direct messages to a user."""

import os
from typing import Any, Dict

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from modules.slack.inputs import SlackMessageString
from src.utils.setup.logger import get_logger
from modules.slack.utils import send_slack_message

logger = get_logger(__name__)


class SlackDMNotifier(BaseNode):
    """Node that sends Slack direct messages to a user."""

    def __init__(
        self,
        slack_message: Resolvable[SlackMessageString] = "{{slack_message}}",
        slack_user_id: Resolvable[str] = "{{SLACK_USER_ID}}",
        thread_ts: Resolvable[str] = "{{thread_sent_ts}}",
        **kwargs
    ):
        """Initialize the SlackDMNotifier node.

        Args:
            slack_message: Message to send (template supported).
            slack_user_id: ID of the user to notify (template supported).
            thread_ts: Optional thread ID to reply to a specific message thread.
            **kwargs: Additional properties from LiteGraph.
        """
        super().__init__(**kwargs)
        self.slack_message = slack_message
        self.slack_user_id = slack_user_id
        self.thread_ts = thread_ts

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic."""
        slack_message = self._slack_message
        if not slack_message:
            logger.info("slack_message is empty, skipping Slack DM notification")
            return {}

        slack_user_id = self._slack_user_id
        if not slack_user_id:
            logger.info("slack_user_id not provided and SLACK_USER_ID not set in env, skipping Slack DM notification")
            return {}

        thread_ts = self._thread_ts
        # Normalize: if thread_ts is the template string itself, treat as None
        if thread_ts == "{{thread_sent_ts}}":
            thread_ts = None

        result = send_slack_message(slack_user_id, slack_message, thread_ts=thread_ts)
        logger.info("Sent Slack DM to %s", slack_user_id)

        # Extract the timestamp (ts) to allow downstream nodes to thread
        slack_ts = result.get("ts")
        # The thread ID is either the existing one we replied to, or the ts of this new message
        final_thread_ts = thread_ts if thread_ts else slack_ts

        return {
            "slack_sent_ts": slack_ts,
            "thread_sent_ts": final_thread_ts
        }
