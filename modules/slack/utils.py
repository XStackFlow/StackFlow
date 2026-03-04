"""Utility functions for sending Slack messages."""

import os
from typing import Dict, Any

import requests

from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


def _resolve_channel_id(user_id: str) -> str:
    """Resolve a user ID or channel ID to a channel/DM ID."""
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    user_id_clean = user_id.lstrip("@").strip()

    # Check if ID is a channel ID (starts with C, G, or D)
    if any(user_id_clean.startswith(prefix) for prefix in ["C", "G", "D"]):
        return user_id_clean

    # It's likely a user ID, open a DM
    open_url = "https://slack.com/api/conversations.open"
    headers = {"Authorization": f"Bearer {slack_token}"}
    try:
        res = requests.post(open_url, headers=headers, json={"users": user_id_clean}, timeout=10)
        res.raise_for_status()
        data = res.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack API error opening DM: {data.get('error')}")
        channel_id = data.get("channel", {}).get("id")
        if not channel_id:
            raise RuntimeError("Could not get channel ID from Slack open conversation response")
        return channel_id
    except Exception as e:
        logger.error("Failed to resolve channel for user %s: %s", user_id_clean, e)
        raise RuntimeError(f"Failed to resolve channel: {e}") from e


def send_slack_message(user_id: str, message: str, thread_ts: str = None) -> Dict[str, Any]:
    """Send a Slack message to a user or channel.
    
    Args:
        user_id: Slack user ID (U...), channel ID (C..., G...), or DM ID (D...)
        message: Message text to send
        thread_ts: Optional thread timestamp to reply to a specific thread
        
    Returns:
        The Slack API response dictionary
        
    Raises:
        RuntimeError: If Slack API call fails
    """
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_token:
        logger.warning("SLACK_BOT_TOKEN not set, skipping Slack notification")
        return {"ok": False, "error": "SLACK_BOT_TOKEN not set"}
    
    channel_id = _resolve_channel_id(user_id)
    headers = {
        "Authorization": f"Bearer {slack_token}",
        "Content-Type": "application/json",
    }
    
    # Send message
    post_url = "https://slack.com/api/chat.postMessage"
    
    # Unescape literal \n if they exist
    processed_message = message.replace("\\n", "\n")
    
    post_payload = {
        "channel": channel_id,
        "text": processed_message,
    }
    
    # Ensure thread_ts is a non-empty string and not the template itself
    if thread_ts:
        ts_str = str(thread_ts).strip()
        if ts_str and not ts_str.startswith("{{") and ts_str != "None":
            post_payload["thread_ts"] = ts_str
    
    try:
        logger.info("Posting to Slack: channel=%s, msg_len=%d, thread_ts=%s", 
                    channel_id, len(processed_message), post_payload.get("thread_ts"))
        post_response = requests.post(post_url, headers=headers, json=post_payload, timeout=10)
        post_response.raise_for_status()
        post_result = post_response.json()
        
        if not post_result.get("ok"):
            error = post_result.get("error", "Unknown error")
            logger.error("Slack API error posting message to %s: %s. Full response: %s", channel_id, error, post_result)
            raise RuntimeError(f"Slack API error posting message: {error}")
        
        logger.info("Sent Slack message to %s (ts: %s, thread_ts: %s)", channel_id, post_result.get("ts"), post_payload.get("thread_ts"))
        return post_result
    except requests.RequestException as e:
        logger.error("Failed to post Slack message: %s", e)
        raise RuntimeError(f"Failed to post Slack message: {e}") from e


def get_slack_thread_replies(user_id: str, thread_ts: str) -> list[Dict[str, Any]]:
    """Fetch all replies in a Slack thread.
    
    Args:
        user_id: Slack user ID, channel ID, or DM ID
        thread_ts: The timestamp of the parent message
        
    Returns:
        List of message dictionaries from the thread
        
    Raises:
        RuntimeError: If Slack API call fails
    """
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_token:
        logger.warning("SLACK_BOT_TOKEN not set, cannot fetch Slack replies")
        return []
        
    channel_id = _resolve_channel_id(user_id)
    headers = {
        "Authorization": f"Bearer {slack_token}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    
    # Fetch replies
    replies_url = "https://slack.com/api/conversations.replies"
    params = {
        "channel": channel_id,
        "ts": thread_ts,
    }
    
    try:
        response = requests.get(replies_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        result = response.json()
        
        if not result.get("ok"):
            error = result.get("error", "Unknown error")
            logger.error("Slack API error fetching replies from %s (ts: %s): %s", channel_id, thread_ts, error)
            raise RuntimeError(f"Slack API error fetching replies: {error}")
            
        messages = result.get("messages", [])
        logger.debug("Fetched %d messages from Slack thread %s in %s", len(messages), thread_ts, channel_id)
        return messages
    except requests.RequestException as e:
        logger.error("Failed to fetch Slack replies: %s", e)
        raise RuntimeError(f"Failed to fetch Slack replies: {e}") from e


def get_slack_history(user_id: str, limit: int = 10) -> list[Dict[str, Any]]:
    """Fetch history of a Slack conversation (DM or channel).
    
    Args:
        user_id: Slack user ID, channel ID, or DM ID
        limit: Max messages to return
        
    Returns:
        List of message dictionaries, latest first
        
    Raises:
        RuntimeError: If Slack API call fails
    """
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_token:
        logger.warning("SLACK_BOT_TOKEN not set, cannot fetch Slack history")
        return []
        
    channel_id = _resolve_channel_id(user_id)
    headers = {
        "Authorization": f"Bearer {slack_token}",
    }
    
    history_url = "https://slack.com/api/conversations.history"
    params = {
        "channel": channel_id,
        "limit": limit,
    }
    
    try:
        response = requests.get(history_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        result = response.json()
        
        if not result.get("ok"):
            error = result.get("error", "Unknown error")
            logger.error("Slack API error fetching history from %s: %s", channel_id, error)
            raise RuntimeError(f"Slack API error fetching history: {error}")
            
        messages = result.get("messages", [])
        logger.debug("Fetched %d messages from Slack history in %s", len(messages), channel_id)
        return messages
    except requests.RequestException as e:
        logger.error("Failed to fetch Slack history: %s", e)
        raise RuntimeError(f"Failed to fetch Slack history: {e}") from e


def add_slack_reaction(user_id: str, timestamp: str, emoji: str) -> Dict[str, Any]:
    """Add a reaction (emoji) to a Slack message.
    
    Args:
        user_id: Slack user ID, channel ID, or DM ID where the message exists
        timestamp: The timestamp (ts) of the message to react to
        emoji: Emoji name (without colons, e.g. 'rocket')
        
    Returns:
        The Slack API response dictionary
        
    Raises:
        RuntimeError: If Slack API call fails
    """
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_token:
        logger.warning("SLACK_BOT_TOKEN not set, cannot add reaction")
        return {"ok": False, "error": "SLACK_BOT_TOKEN not set"}
        
    channel_id = _resolve_channel_id(user_id)
    headers = {
        "Authorization": f"Bearer {slack_token}",
        "Content-Type": "application/json",
    }
    
    reaction_url = "https://slack.com/api/reactions.add"
    payload = {
        "channel": channel_id,
        "timestamp": timestamp,
        "name": emoji.strip(":"),
    }
    
    try:
        logger.info("Adding reaction to Slack: channel=%s, ts=%s, emoji=%s", 
                    channel_id, timestamp, payload["name"])
        response = requests.post(reaction_url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        
        if not result.get("ok"):
            error = result.get("error", "Unknown error")
            # "already_reacted" is a common expected error if we retry nodes
            if error == "already_reacted":
                logger.info("Slack message %s already has reaction %s", timestamp, emoji)
                return {"ok": True, "already_reacted": True}
                
            logger.error("Slack API error adding reaction to %s (ts: %s): %s", channel_id, timestamp, error)
            raise RuntimeError(f"Slack API error adding reaction: {error}")
            
        logger.info("Added reaction %s to Slack message %s", emoji, timestamp)
        return result
    except requests.RequestException as e:
        logger.error("Failed to add Slack reaction: %s", e)
        raise RuntimeError(f"Failed to add Slack reaction: {e}") from e


def remove_slack_reaction(user_id: str, timestamp: str, emoji: str) -> Dict[str, Any]:
    """Remove a reaction (emoji) from a Slack message.
    
    Args:
        user_id: Slack user ID, channel ID, or DM ID where the message exists
        timestamp: The timestamp (ts) of the message to remove reaction from
        emoji: Emoji name (without colons, e.g. 'rocket')
        
    Returns:
        The Slack API response dictionary
        
    Raises:
        RuntimeError: If Slack API call fails
    """
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_token:
        logger.warning("SLACK_BOT_TOKEN not set, cannot remove reaction")
        return {"ok": False, "error": "SLACK_BOT_TOKEN not set"}
        
    channel_id = _resolve_channel_id(user_id)
    headers = {
        "Authorization": f"Bearer {slack_token}",
        "Content-Type": "application/json",
    }
    
    reaction_url = "https://slack.com/api/reactions.remove"
    payload = {
        "channel": channel_id,
        "timestamp": timestamp,
        "name": emoji.strip(":"),
    }
    
    try:
        logger.info("Removing reaction from Slack: channel=%s, ts=%s, emoji=%s", 
                    channel_id, timestamp, payload["name"])
        response = requests.post(reaction_url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        result = response.json()
        
        if not result.get("ok"):
            error = result.get("error", "Unknown error")
            # "no_reaction" is a common expected error if the reaction is already gone
            if error == "no_reaction":
                logger.info("Slack message %s already has no reaction %s", timestamp, emoji)
                return {"ok": True, "no_reaction": True}
                
            logger.error("Slack API error removing reaction from %s (ts: %s): %s", channel_id, timestamp, error)
            raise RuntimeError(f"Slack API error removing reaction: {error}")
            
        logger.info("Removed reaction %s from Slack message %s", emoji, timestamp)
        return result
    except requests.RequestException as e:
        logger.error("Failed to remove Slack reaction: %s", e)
        raise RuntimeError(f"Failed to remove Slack reaction: {e}") from e


def get_slack_reactions(user_id: str, timestamp: str) -> list[Dict[str, Any]]:
    """Get all reactions on a Slack message.

    Args:
        user_id: Slack user ID, channel ID, or DM ID where the message exists
        timestamp: The timestamp (ts) of the message to get reactions for

    Returns:
        List of reaction dicts, each with 'name' (emoji) and 'users' (list of user IDs).
        Returns empty list if no reactions or on error.
    """
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_token:
        logger.warning("SLACK_BOT_TOKEN not set, cannot get reactions")
        return []

    channel_id = _resolve_channel_id(user_id)
    headers = {
        "Authorization": f"Bearer {slack_token}",
    }

    reactions_url = "https://slack.com/api/reactions.get"
    params = {
        "channel": channel_id,
        "timestamp": timestamp,
        "full": "true",
    }

    try:
        response = requests.get(reactions_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            error = result.get("error", "Unknown error")
            # "no_item_specified" or similar means the message doesn't exist
            logger.error("Slack API error getting reactions from %s (ts: %s): %s", channel_id, timestamp, error)
            return []

        message = result.get("message", {})
        reactions = message.get("reactions", [])
        logger.debug("Fetched %d reaction types on message %s in %s", len(reactions), timestamp, channel_id)
        return reactions
    except requests.RequestException as e:
        logger.error("Failed to get Slack reactions: %s", e)
        return []

