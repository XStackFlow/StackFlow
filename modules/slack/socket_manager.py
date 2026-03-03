"""Persistent Slack Socket Mode connection manager.

A single WebSocket connection is shared across all SlackReplyListener node
executions. The manager runs in a dedicated background daemon thread with its
own asyncio event loop, so it never conflicts with the main graph event loop.

Usage:
    from modules.slack.socket_manager import get_socket_manager

    event = get_socket_manager().wait_for_reply(user_id, thread_ts, timeout_seconds)
"""

import asyncio
import os
import queue
import threading
from typing import Any, Dict, List, Optional, Tuple

from src.utils.setup.logger import get_logger

logger = get_logger(__name__)

# Waiter key: (user_id_clean, thread_ts) for thread replies,
#             (user_id_clean, None)      for top-level DM / channel messages
_WaiterKey = Tuple[str, Optional[str]]


class SlackSocketManager:
    """Maintains a single persistent Socket Mode WebSocket to Slack.

    Lazy-starts on the first call to wait_for_reply(). Reconnection is handled
    automatically by the Slack Bolt handler.
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._start_error: Optional[Exception] = None
        self._start_lock = threading.Lock()
        self._waiters: Dict[_WaiterKey, List[queue.Queue]] = {}
        self._waiters_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def wait_for_reply(
        self,
        user_id: str,
        thread_ts: Optional[str],
        timeout_seconds: float,
    ) -> Optional[Dict[str, Any]]:
        """Block until a matching Slack message arrives, then return it.

        Starts the background socket thread on the first call.
        Returns None on timeout.

        Args:
            user_id:         Clean Slack user ID (no leading @).
            thread_ts:       Thread timestamp to match, or None for DM/channel.
            timeout_seconds: How long to wait before giving up.
        """
        # Register the waiter BEFORE starting the socket so that any event
        # arriving the moment the connection opens is captured.
        waiter_q: queue.Queue = queue.Queue()
        key: _WaiterKey = (user_id, thread_ts)

        with self._waiters_lock:
            self._waiters.setdefault(key, []).append(waiter_q)

        try:
            self._ensure_started()
            return waiter_q.get(timeout=timeout_seconds)
        except queue.Empty:
            return None
        finally:
            with self._waiters_lock:
                waiters = self._waiters.get(key, [])
                try:
                    waiters.remove(waiter_q)
                except ValueError:
                    pass
                if not waiters:
                    self._waiters.pop(key, None)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Start the background thread if not already running."""
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return

            self._ready.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._run_background_loop,
                daemon=True,
                name="slack-socket-manager",
            )
            self._thread.start()

        self._ready.wait(timeout=15)

        if self._start_error:
            raise RuntimeError(f"SlackSocketManager failed to connect: {self._start_error}")
        if not self._ready.is_set():
            raise RuntimeError("SlackSocketManager timed out connecting to Slack (15s)")

    def _run_background_loop(self) -> None:
        """Entry point for the background daemon thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._connect_and_serve())
        except Exception as e:
            self._start_error = e
            self._ready.set()
            logger.error("SlackSocketManager: Background loop crashed: %s", e)
        finally:
            loop.close()
            self._loop = None

    async def _connect_and_serve(self) -> None:
        """Connect to Slack Socket Mode and keep the connection alive."""
        from slack_bolt.async_app import AsyncApp
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

        slack_bot_token = os.environ.get("SLACK_BOT_TOKEN")
        slack_app_token = os.environ.get("SLACK_APP_TOKEN")

        if not slack_bot_token or not slack_app_token:
            raise RuntimeError("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must both be set")

        app = AsyncApp(token=slack_bot_token)

        @app.event("message")
        async def handle_message(event: Dict[str, Any]) -> None:
            self._dispatch(event)

        @app.event("reaction_added")
        async def handle_reaction(event: Dict[str, Any]) -> None:
            self._dispatch_reaction(event)

        handler = AsyncSocketModeHandler(app, slack_app_token)
        await handler.connect_async()
        logger.info("SlackSocketManager: Connected — single WebSocket shared by all listeners")
        self._ready.set()

        try:
            while True:
                await asyncio.sleep(30)
        finally:
            await handler.close_async()
            logger.info("SlackSocketManager: Disconnected")

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, event: Dict[str, Any]) -> None:
        """Route a Slack message event to all matching waiters."""
        user = event.get("user")
        if not user:
            return

        ts = event.get("ts")
        thread_ts = event.get("thread_ts")

        if thread_ts and thread_ts != ts:
            # Reply inside a thread — match on (user, thread_ts)
            key: _WaiterKey = (user, thread_ts)
        else:
            # Top-level DM or channel message — match on (user, None)
            key = (user, None)

        with self._waiters_lock:
            for q in self._waiters.get(key, []):
                q.put_nowait(event)

    def _dispatch_reaction(self, event: Dict[str, Any]) -> None:
        """Route a Slack reaction_added event to all matching waiters.

        Uses (user, item.ts) as the waiter key — this matches waiters
        registered for replies to the message that was reacted to.
        """
        user = event.get("user")
        if not user:
            return

        item = event.get("item", {})
        item_ts = item.get("ts")
        if not item_ts:
            return

        key: _WaiterKey = (user, item_ts)

        reaction_event = {
            "type": "reaction_added",
            "user": user,
            "reaction": event.get("reaction", ""),
            "item_ts": item_ts,
            "ts": event.get("event_ts", ""),
        }

        with self._waiters_lock:
            for q in self._waiters.get(key, []):
                q.put_nowait(reaction_event)


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_instance: Optional[SlackSocketManager] = None
_instance_lock = threading.Lock()


def get_socket_manager() -> SlackSocketManager:
    """Return the process-wide SlackSocketManager singleton."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = SlackSocketManager()
    return _instance
