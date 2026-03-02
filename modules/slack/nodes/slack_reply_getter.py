"""Slack Reply Getter - Fetches the latest reply from a Slack thread."""

from typing import Any, Dict, List, Optional
import os

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger
from modules.slack.utils import get_slack_thread_replies, get_slack_history

logger = get_logger(__name__)


class SlackReplyGetter(BaseNode):
    """Node that fetches the latest user reply from a Slack thread."""

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
            logger.info("SlackReplyGetter: thread_ts not provided or empty, polling latest messages from channel for %s", slack_user_id_clean)
        else:
            logger.info("SlackReplyGetter: Starting poll for replies from %s in thread %s (timeout: %d min)", 
                        slack_user_id_clean, thread_ts, int(self._timeout_minutes))

        start_time = time.time()
        timeout_seconds = int(self._timeout_minutes) * 60
        check_interval = int(self._check_interval_seconds)
        
        while True:
            try:
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
                        
                        # Fetch context history for the LLM
                        context_messages = get_slack_history(slack_user_id, limit=50)

                        last_action_ts = state.get("last_action_ts")
                        if last_action_ts:
                            logger.info("SlackReplyGetter: Filtering history since last_action_ts: %s", last_action_ts)
                            context_messages = [m for m in context_messages if float(m["ts"]) > float(last_action_ts)]

                        # Messages are returned latest first; reverse for chronological order
                        history = []
                        for msg in reversed(context_messages):
                            t = msg.get("text", "").strip()
                            if t:
                                role = "User" if msg.get("user") == slack_user_id_clean else "Assistant"
                                history.append({"role": role, "content": t})

                        return {
                            "last_slack_reply": latest_text,
                            "last_reply_ts": ts,
                            "slack_conversation_history": history,
                        }
                    else:
                        reason = "bot" if (latest_msg.get("bot_id") or latest_msg.get("subtype") == "bot_message") else f"user {latest_user}"
                        logger.debug("SlackReplyGetter: Latest message is from %s, not target %s. Waiting %d seconds...", 
                                     reason, slack_user_id_clean, check_interval)
                else:
                    scope = f"thread {thread_ts}" if is_thread else "history"
                    logger.debug("SlackReplyGetter: No messages found in %s. Waiting %d seconds...", scope, check_interval)
                
                # Check for timeout
                elapsed = time.time() - start_time
                if elapsed > timeout_seconds:
                    logger.warning("SlackReplyGetter: Timed out waiting for reply")
                    return {
                        "last_slack_reply": ""
                    }
                
                # Sleep and try again
                time.sleep(check_interval)
                
            except Exception as e:
                logger.error("SlackReplyGetter: Error during poll: %s", e)
                time.sleep(check_interval)
                
                elapsed = time.time() - start_time
                if elapsed > timeout_seconds:
                    return {
                        "last_slack_reply": ""
                    }


