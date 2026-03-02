"""Thread-safe tool context using contextvars.

This module provides a per-coroutine/per-thread context for tool execution,
eliminating the need for os.chdir() which is process-wide and unsafe when
multiple LLM agents run in parallel.

Usage:
    # In the executor (langchain.py):
    with repo_path_context(Path("/path/to/repo")):
        agent.invoke(...)

    # In any tool:
    from modules.llm.tools.tool_context import get_repo_path
    cwd = get_repo_path()  # Returns the repo path for the current execution context
"""

import os
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

# ContextVar is inherited by asyncio tasks and copied for threads started via asyncio.to_thread
_repo_path_var: ContextVar[Optional[Path]] = ContextVar("repo_path", default=None)


@contextmanager
def repo_path_context(path: Path):
    """Context manager that sets the repo_path for the current execution context.

    This is safe for parallel execution because contextvars are per-coroutine
    and automatically copied to threads created via asyncio.to_thread().
    """
    token = _repo_path_var.set(path)
    try:
        yield
    finally:
        _repo_path_var.reset(token)


def get_repo_path() -> Path:
    """Get the repo path for the current execution context.

    Falls back to os.getcwd() if no context is set (e.g., when running tools standalone).
    """
    path = _repo_path_var.get()
    if path is not None:
        return path
    return Path(os.getcwd())


def resolve_path(file_path: str) -> str:
    """Resolve a potentially relative file path against the current repo context.

    If the file_path is already absolute, returns it as-is.
    Otherwise, resolves it relative to the current repo_path context.
    """
    if os.path.isabs(file_path):
        return file_path
    return str(get_repo_path() / file_path)
