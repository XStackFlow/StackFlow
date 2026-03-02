"""Database utilities for StackFlow checkpointer (PostgreSQL)."""

import os
from contextlib import asynccontextmanager
from src.utils.exceptions import ConfigurationError


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
    """Async context manager that yields an AsyncPostgresSaver."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    async with AsyncPostgresSaver.from_conn_string(get_conn_string()) as cp:
        await cp.setup()
        yield cp
