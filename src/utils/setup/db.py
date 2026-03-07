"""Database utilities for StackFlow checkpointer (PostgreSQL)."""

import asyncio
import os
from contextlib import asynccontextmanager
from src.utils.exceptions import ConfigurationError
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 5
RETRY_BACKOFF = [1, 2, 4, 8, 16]  # seconds between retries


def get_db_config():
    """Load and validate database environment configuration."""
    required_configs = [
        "LANGGRAPH_DB_USER",
        "LANGGRAPH_DB_PASSWORD",
        "LANGGRAPH_DB_NAME",
        "LANGGRAPH_DB_HOST",
        "LANGGRAPH_DB_PORT",
    ]

    config = {}
    missing_config = []

    for key in required_configs:
        value = os.getenv(key)
        if not value:
            missing_config.append(key)
        config[key] = value

    if missing_config:
        raise ConfigurationError(f"Missing required database environment variables: {', '.join(missing_config)}")

    return config


def get_conn_string():
    """Get PostgreSQL connection string with keepalive settings.

    TCP keepalives prevent the OS/firewall from dropping idle connections
    during long-running graph executions (e.g., waiting on LLM or human review).
    """
    config = get_db_config()
    base = (
        f"postgresql://{config['LANGGRAPH_DB_USER']}:{config['LANGGRAPH_DB_PASSWORD']}"
        f"@{config['LANGGRAPH_DB_HOST']}:{config['LANGGRAPH_DB_PORT']}/{config['LANGGRAPH_DB_NAME']}"
    )
    # keepalives=1: enable TCP keepalive
    # keepalives_idle=60: send first keepalive after 60s idle
    # keepalives_interval=15: retry every 15s
    # keepalives_count=4: give up after 4 missed keepalives
    return f"{base}?keepalives=1&keepalives_idle=60&keepalives_interval=15&keepalives_count=4"


@asynccontextmanager
async def create_checkpointer():
    """Async context manager that yields an AsyncPostgresSaver.

    Retries connection establishment up to MAX_RETRIES times with exponential
    backoff when the connection fails (refused, closed, timeout, etc.).
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    import psycopg

    last_err = None
    cp_context = None

    # Retry loop — only retries connection establishment, not usage errors
    for attempt in range(MAX_RETRIES):
        try:
            cp_context = AsyncPostgresSaver.from_conn_string(get_conn_string())
            cp = await cp_context.__aenter__()
            await cp.setup()
            break  # connected
        except (
            psycopg.OperationalError,
            psycopg.InterfaceError,
            ConnectionError,
            OSError,
        ) as e:
            last_err = e
            # Clean up the failed context manager
            if cp_context:
                try:
                    await cp_context.__aexit__(type(e), e, e.__traceback__)
                except Exception:
                    pass
                cp_context = None
            delay = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            logger.warning(
                "Postgres connection failed (attempt %d/%d): %s — retrying in %ds",
                attempt + 1, MAX_RETRIES, e, delay,
            )
            await asyncio.sleep(delay)
    else:
        raise ConnectionError(
            f"Failed to connect to PostgreSQL after {MAX_RETRIES} attempts: {last_err}"
        )

    # Yield the checkpointer and clean up on exit
    try:
        yield cp
    finally:
        await cp_context.__aexit__(None, None, None)


@asynccontextmanager
async def get_checkpointer(app=None):
    """Get a working checkpointer — prefers the shared one, falls back to fresh.

    1. If app has a shared checkpointer, health-checks it first.
    2. If the shared connection is dead (or no app), creates a fresh connection
       via create_checkpointer(), stores it as the new shared checkpointer,
       and cleans up the old one.
    """
    shared_cp = getattr(app.state, "checkpointer", None) if app else None

    if shared_cp:
        # Verify the shared connection is still alive.
        # shared_cp.conn is a raw AsyncConnection (not a pool), so use it directly.
        healthy = False
        try:
            if shared_cp.conn.closed:
                raise ConnectionError("connection already closed")
            await shared_cp.conn.execute("SELECT 1")
            healthy = True
        except Exception as e:
            logger.warning("Shared Postgres connection lost (%s) — reconnecting", e)

        if healthy:
            try:
                yield shared_cp
            except BaseException:
                # Mark shared connection as dead so next call reconnects
                if app:
                    app.state.checkpointer = None
                raise
            return

        # Close the dead shared connection to free the slot
        try:
            await shared_cp.conn.close()
        except Exception:
            pass
        if app:
            app.state.checkpointer = None

    # Create a fresh connection and promote it to shared
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    import psycopg

    last_err = None
    cp_context = None
    for attempt in range(MAX_RETRIES):
        try:
            cp_context = AsyncPostgresSaver.from_conn_string(get_conn_string())
            cp = await cp_context.__aenter__()
            await cp.setup()
            break  # connected successfully
        except (
            psycopg.OperationalError,
            psycopg.InterfaceError,
            ConnectionError,
            OSError,
        ) as e:
            last_err = e
            if cp_context:
                try:
                    await cp_context.__aexit__(type(e), e, e.__traceback__)
                except Exception:
                    pass
                cp_context = None
            delay = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            logger.warning(
                "Postgres reconnect failed (attempt %d/%d): %s — retrying in %ds",
                attempt + 1, MAX_RETRIES, e, delay,
            )
            await asyncio.sleep(delay)
    else:
        raise ConnectionError(
            f"Failed to reconnect to PostgreSQL after {MAX_RETRIES} attempts: {last_err}"
        )

    # Store as new shared checkpointer so future calls reuse it
    if app:
        app.state.checkpointer = cp
        app.state._checkpointer_context = cp_context

    try:
        yield cp
    except BaseException:
        # Connection died during use — invalidate shared reference
        if app:
            app.state.checkpointer = None
        raise
