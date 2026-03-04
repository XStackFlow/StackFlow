"""Slack Reply Getter - Polls for text replies and emoji reactions from a Slack thread."""

from typing import Any, Dict, List, Optional, Set, Tuple
import os

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger
from modules.slack.utils import get_slack_thread_replies, get_slack_history, get_slack_reactions


logger = get_logger(__name__)


class SlackReplyGetter(BaseNode):
    """Node that polls for user replies or emoji reactions from Slack.

    Supports both:
    - Text message replies (in a thread or top-level DM/channel)
    - Emoji reactions on the thread parent message (returned as ":emoji:" format)
    """

    def __init__(
        self,
        slack_user_id: Resolvable[str] = "{{SLACK_USER_ID}}",
        thread_ts: Resolvable[str] = "{{thread_sent_ts}}",
        check_interval_seconds: Resolvable[int] = 10,
        timeout_minutes: Resolvable[int] = 1440,  # Default to 24 hours
        **kwargs
    ):
        """Initialize the SlackReplyGetter node.

        Args:
            slack_user_id: ID of the user or channel (template supported).
            thread_ts: The timestamp of the thread (template supported).
            check_interval_seconds: How often to poll Slack for new messages.
            timeout_minutes: Maximum time to wait for a reply.
            **kwargs: Additional properties from LiteGraph.
        """
        super().__init__(**kwargs)
        self.slack_user_id = slack_user_id
        self.thread_ts = thread_ts
        self.check_interval_seconds = check_interval_seconds
        self.timeout_minutes = timeout_minutes

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic with polling."""
        import time

        slack_user_id = self._slack_user_id
        if not slack_user_id:
            logger.warning("SlackReplyGetter: slack_user_id not provided, skipping")
            return {}

        # Clean user ID for comparison
        slack_user_id_clean = slack_user_id.lstrip("@").strip()

        thread_ts = self._thread_ts
        is_thread = True
        if not thread_ts or thread_ts == "{{thread_sent_ts}}" or str(thread_ts).strip() == "" or str(thread_ts) == "None":
            is_thread = False
            logger.debug("SlackReplyGetter: thread_ts not provided or empty, polling latest messages from channel for %s", slack_user_id_clean)
        else:
            logger.debug("SlackReplyGetter: Starting poll for replies/reactions from %s in thread %s (timeout: %d min)",
                        slack_user_id_clean, thread_ts, int(self._timeout_minutes))

        start_time = time.time()
        timeout_seconds = int(self._timeout_minutes) * 60
        check_interval = int(self._check_interval_seconds)

        # Snapshot existing reactions so we only detect NEW ones
        baseline_reactions: Set[Tuple[str, str]] = set()  # {(emoji_name, user_id), ...}
        if is_thread:
            baseline_reactions = self._snapshot_reactions(slack_user_id, thread_ts)
            logger.debug("SlackReplyGetter: Baseline reactions snapshot: %d reaction(s)", len(baseline_reactions))

        while True:
            # --- Check for text messages ---
            if is_thread:
                messages = get_slack_thread_replies(slack_user_id, thread_ts)
                # We need at least one reply (messages[0] is the parent)
                if len(messages) > 1:
                    messages_to_check = [messages[-1]]
                else:
                    messages_to_check = []
            else:
                # Fetch only the absolute latest message to check if the conversation
                # currently ends with a message from the target user.
                messages = get_slack_history(slack_user_id, limit=1)
                messages_to_check = messages

            if messages_to_check:
                latest_msg = messages_to_check[0]
                latest_user = latest_msg.get("user")
                latest_text = latest_msg.get("text", "").strip()
                ts = latest_msg.get("ts")

                logger.debug("SlackReplyGetter: Latest message in %s is from %s at %s: '%s...'",
                             "thread" if is_thread else "history", latest_user, ts, latest_text[:20])

                if latest_user == slack_user_id_clean:
                    logger.info("SlackReplyGetter: Found expected user reply: %s", latest_text)
                    return {
                        "last_slack_reply": latest_text,
                        "last_reply_ts": ts,
                    }
                else:
                    reason = "bot" if (latest_msg.get("bot_id") or latest_msg.get("subtype") == "bot_message") else f"user {latest_user}"
                    logger.debug("SlackReplyGetter: Latest message is from %s, not target %s. Waiting...",
                                 reason, slack_user_id_clean)

            # --- Check for new emoji reactions (thread mode only) ---
            if is_thread:
                new_reaction = self._check_new_reactions(
                    slack_user_id, thread_ts, slack_user_id_clean, baseline_reactions
                )
                if new_reaction:
                    emoji, reaction_user = new_reaction
                    logger.info("SlackReplyGetter: Detected new reaction :%s: from %s", emoji, reaction_user)
                    return {
                        "last_slack_reply": f":{emoji}:",
                        "last_reply_ts": thread_ts,
                    }

            # Check for timeout
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                logger.warning("SlackReplyGetter: Timed out waiting for reply")
                return {
                    "last_slack_reply": ""
                }

            # Sleep and try again
            time.sleep(check_interval)

    @staticmethod
    def _snapshot_reactions(slack_user_id: str, thread_ts: str) -> Set[Tuple[str, str]]:
        """Take a snapshot of current reactions as (emoji_name, user_id) pairs."""
        snapshot: Set[Tuple[str, str]] = set()
        try:
            reactions = get_slack_reactions(slack_user_id, thread_ts)
            for r in reactions:
                emoji = r.get("name", "")
                for uid in r.get("users", []):
                    snapshot.add((emoji, uid))
        except Exception as e:
            logger.warning("SlackReplyGetter: Failed to snapshot reactions: %s", e)
        return snapshot

    @staticmethod
    def _check_new_reactions(
        slack_user_id: str,
        thread_ts: str,
        target_user_id: str,
        baseline: Set[Tuple[str, str]],
    ) -> Optional[Tuple[str, str]]:
        """Check for new reactions from the target user since baseline.

        Returns (emoji_name, user_id) if a new reaction is found, else None.
        """
        try:
            reactions = get_slack_reactions(slack_user_id, thread_ts)
            for r in reactions:
                emoji = r.get("name", "")
                for uid in r.get("users", []):
                    if uid == target_user_id and (emoji, uid) not in baseline:
                        return (emoji, uid)
        except Exception as e:
            logger.debug("SlackReplyGetter: Error checking reactions: %s", e)
        return None
