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
    """Get PostgreSQL connection string."""
    config = get_db_config()
    return (
        f"postgresql://{config['LANGGRAPH_DB_USER']}:{config['LANGGRAPH_DB_PASSWORD']}"
        f"@{config['LANGGRAPH_DB_HOST']}:{config['LANGGRAPH_DB_PORT']}/{config['LANGGRAPH_DB_NAME']}"
    )


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
       via create_checkpointer() (which retries on failure).
    """
    import psycopg

    shared_cp = getattr(app.state, "checkpointer", None) if app else None

    if shared_cp:
        # Verify the shared connection is still alive
        healthy = False
        try:
            async with shared_cp.conn.connection(timeout=3) as conn:
                await conn.execute("SELECT 1")
            healthy = True
        except Exception as e:
            logger.warning("Shared Postgres connection lost (%s) — reconnecting", e)

        if healthy:
            yield shared_cp
            return

    # Fall back to a fresh connection (with retry)
    async with create_checkpointer() as cp:
        yield cp
