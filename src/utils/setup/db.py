"""Database utilities for StackFlow checkpointer.

Supports SQLite (default) and PostgreSQL backends.
Set LANGGRAPH_CHECKPOINTER=postgres to use PostgreSQL.
SQLite stores checkpoints in data/checkpoints.db (no external dependencies).
"""

import asyncio
import os
from pathlib import Path
from contextlib import asynccontextmanager
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 5
RETRY_BACKOFF = [1, 2, 4, 8, 16]

SQLITE_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "checkpoints.db"


def _use_postgres() -> bool:
    return os.getenv("LANGGRAPH_CHECKPOINTER", "").lower() == "postgres"


# ── PostgreSQL helpers ────────────────────────────────────────────────

def get_db_config():
    """Load and validate database environment configuration."""
    from src.utils.exceptions import ConfigurationError

    required_configs = [
        "LANGGRAPH_DB_USER",
        "LANGGRAPH_DB_PASSWORD",
        "LANGGRAPH_DB_NAME",
        "LANGGRAPH_DB_HOST",
        "LANGGRAPH_DB_PORT",
    ]
    config = {}
    missing = []
    for key in required_configs:
        value = os.getenv(key)
        if not value:
            missing.append(key)
        config[key] = value
    if missing:
        raise ConfigurationError(f"Missing required database environment variables: {', '.join(missing)}")
    return config


def get_conn_string():
    """Get PostgreSQL connection string with keepalive settings."""
    config = get_db_config()
    base = (
        f"postgresql://{config['LANGGRAPH_DB_USER']}:{config['LANGGRAPH_DB_PASSWORD']}"
        f"@{config['LANGGRAPH_DB_HOST']}:{config['LANGGRAPH_DB_PORT']}/{config['LANGGRAPH_DB_NAME']}"
    )
    return f"{base}?keepalives=1&keepalives_idle=60&keepalives_interval=15&keepalives_count=4"


@asynccontextmanager
async def _create_postgres_checkpointer():
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    import psycopg

    last_err = None
    cp_context = None
    for attempt in range(MAX_RETRIES):
        try:
            cp_context = AsyncPostgresSaver.from_conn_string(get_conn_string())
            cp = await cp_context.__aenter__()
            await cp.setup()
            break
        except (psycopg.OperationalError, psycopg.InterfaceError, ConnectionError, OSError) as e:
            last_err = e
            if cp_context:
                try:
                    await cp_context.__aexit__(type(e), e, e.__traceback__)
                except Exception:
                    pass
                cp_context = None
            delay = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            logger.warning("Postgres connection failed (attempt %d/%d): %s — retrying in %ds", attempt + 1, MAX_RETRIES, e, delay)
            await asyncio.sleep(delay)
    else:
        raise ConnectionError(f"Failed to connect to PostgreSQL after {MAX_RETRIES} attempts: {last_err}")

    try:
        yield cp
    finally:
        await cp_context.__aexit__(None, None, None)


# ── SQLite helpers ────────────────────────────────────────────────────

@asynccontextmanager
async def _create_sqlite_checkpointer():
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db_path = str(SQLITE_DB_PATH)
    logger.info("Opening SQLite checkpointer at %s", db_path)

    async with AsyncSqliteSaver.from_conn_string(db_path) as cp:
        await cp.setup()
        yield cp


# ── Public API ────────────────────────────────────────────────────────

@asynccontextmanager
async def create_checkpointer():
    """Create a checkpointer — SQLite by default, PostgreSQL if configured."""
    if _use_postgres():
        async with _create_postgres_checkpointer() as cp:
            yield cp
    else:
        async with _create_sqlite_checkpointer() as cp:
            yield cp


@asynccontextmanager
async def get_checkpointer(app=None):
    """Get a working checkpointer — reuses shared instance if available.

    For SQLite: always reuses the shared instance (no health check needed).
    For PostgreSQL: health-checks the shared connection, reconnects if dead.
    """
    shared_cp = getattr(app.state, "checkpointer", None) if app else None

    if shared_cp:
        if _use_postgres():
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
                    if app:
                        app.state.checkpointer = None
                    raise
                return

            try:
                await shared_cp.conn.close()
            except Exception:
                pass
            if app:
                app.state.checkpointer = None
        else:
            # SQLite — just yield the shared instance
            yield shared_cp
            return

    # Create a fresh checkpointer and promote to shared
    if _use_postgres():
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        import psycopg

        last_err = None
        cp_context = None
        for attempt in range(MAX_RETRIES):
            try:
                cp_context = AsyncPostgresSaver.from_conn_string(get_conn_string())
                cp = await cp_context.__aenter__()
                await cp.setup()
                break
            except (psycopg.OperationalError, psycopg.InterfaceError, ConnectionError, OSError) as e:
                last_err = e
                if cp_context:
                    try:
                        await cp_context.__aexit__(type(e), e, e.__traceback__)
                    except Exception:
                        pass
                    cp_context = None
                delay = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                logger.warning("Postgres reconnect failed (attempt %d/%d): %s — retrying in %ds", attempt + 1, MAX_RETRIES, e, delay)
                await asyncio.sleep(delay)
        else:
            raise ConnectionError(f"Failed to reconnect to PostgreSQL after {MAX_RETRIES} attempts: {last_err}")

        if app:
            app.state.checkpointer = cp
            app.state._checkpointer_context = cp_context

        try:
            yield cp
        except BaseException:
            if app:
                app.state.checkpointer = None
            raise
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        cp = AsyncSqliteSaver.from_conn_string(str(SQLITE_DB_PATH))
        saver = await cp.__aenter__()
        await saver.setup()

        if app:
            app.state.checkpointer = saver
            app.state._checkpointer_context = cp

        try:
            yield saver
        except BaseException:
            # SQLite doesn't have connection issues, but clean up on unexpected errors
            if app:
                app.state.checkpointer = None
            raise
