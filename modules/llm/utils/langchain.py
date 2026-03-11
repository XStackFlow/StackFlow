"""LangChain Task Execution Utilities - Executes tasks using LangChain and tools."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.callbacks import BaseCallbackHandler
from langgraph.prebuilt import create_react_agent
from src.utils.setup.const import PROJECT_ROOT
from src.utils.exceptions import RetriableError
from src.utils.setup.logger import get_logger, NAMESPACE_CONTEXT, THREAD_ID_CONTEXT
from modules.llm.tools import (
    git_status,
    git_diff,
    go_build,
    go_test,
    golangci_lint,
    READ_TOOLS,
    WRITE_TOOLS,
    MEMORY_TOOLS,
)
from modules.llm.utils.llm_utils import parse_llm_json, get_system_message
from modules.llm.tools.tool_context import repo_path_context

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Persistent in-memory checkpointers — keyed by outer thread_id so the
# inner ReAct agent can resume its conversation after stop/resume.
# ---------------------------------------------------------------------------
_agent_checkpointers: Dict[str, Any] = {}


def _get_agent_checkpointer(thread_id: str):
    """Return a MemorySaver that persists across calls for the same thread."""
    from langgraph.checkpoint.memory import MemorySaver
    if thread_id not in _agent_checkpointers:
        _agent_checkpointers[thread_id] = MemorySaver()
    return _agent_checkpointers[thread_id]


def clear_agent_checkpointer(thread_id: str):
    """Remove a cached checkpointer when a graph execution finishes."""
    _agent_checkpointers.pop(thread_id, None)


# ---------------------------------------------------------------------------
# Per-agent file logger – records every tool call to a dedicated log file
# Logs go to: logs/graph/<thread_id>/agents/<agent_name>.log
# ---------------------------------------------------------------------------


class AgentFileCallbackHandler(BaseCallbackHandler):
    """Callback handler that writes tool calls and LLM steps to a per-agent log file."""

    def __init__(self, log_path: Path, repo_path: Path):
        self._log_path = log_path
        self._repo_path = repo_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Write header
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"=== Agent Log: {log_path.stem} ===\n")
            f.write(f"Repo path: {repo_path}\n")
            f.write(f"Started: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}\n")
            f.write("=" * 80 + "\n\n")

    def _write(self, text: str):
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        tool_name = serialized.get("name", "unknown_tool")
        ts = datetime.now().strftime("%I:%M:%S %p")
        self._write("=" * 60)
        self._write(f"[{ts}] TOOL CALL: {tool_name}")
        # Log the input (truncate very long values)
        input_display = input_str
        if len(input_display) > 2000:
            input_display = input_display[:2000] + "... (truncated)"
        self._write(f"  Input: {input_display}")

    def on_tool_end(
        self,
        output: str,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        ts = datetime.now().strftime("%I:%M:%S %p")
        output_display = str(output)
        if len(output_display) > 3000:
            output_display = output_display[:3000] + "... (truncated)"
        self._write(f"[{ts}] TOOL RESULT:")
        self._write(f"  Output: {output_display}")
        self._write("")

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        ts = datetime.now().strftime("%I:%M:%S %p")
        self._write(f"[{ts}] TOOL ERROR: {error}")
        self._write("")

def execute_langchain(
    llm: Any,
    repo_path: Path,
    content: str,
    tools: list,
    recursion_limit: int = 25,
    callbacks: list[BaseCallbackHandler] = None,
    run_name: str = "LangGraph Node"
) -> Dict[str, Any]:
    """Execute a task using a LangChain LLM and a specific tool set.

    Args:
        llm: The initialized LangChain LLM instance.
        repo_path: Path to the repository.
        content: Content of the instructions.
        tools: List of tools to provide to the agent.
        recursion_limit: Maximum number of steps the node can take.
        callbacks: Optional list of LangChain callback handlers.
        run_name: Name of the run for tracing.

    Returns:
        JSON result from the execution.

    Raises:
        RetriableError: If LLM output cannot be parsed as JSON.
        RuntimeError: For other execution failures.
    """

    # Use a persistent checkpointer so conversations survive stop/resume
    thread_id_raw = THREAD_ID_CONTEXT.get() or "unknown"
    checkpointer = _get_agent_checkpointer(thread_id_raw)
    _executor = create_react_agent(llm, tools, checkpointer=checkpointer)

    logger.info("Model: %s", llm.__class__.__name__)
    logger.info("Working directory: %s", repo_path.resolve())

    # Create per-agent log file under logs/graph/<thread_id>/agents/
    thread_id = THREAD_ID_CONTEXT.get() or "unknown"
    namespace = NAMESPACE_CONTEXT.get() or "global"
    ts = datetime.now().strftime("%H%M%S")
    safe_run_name = run_name.replace(" ", "_")
    graph_logs_dir = PROJECT_ROOT / "logs" / "graph" / thread_id / "agents"
    agent_log_file = graph_logs_dir / f"{safe_run_name}_{namespace}_{ts}.log"
    agent_handler = AgentFileCallbackHandler(agent_log_file, repo_path.resolve())
    logger.info("Agent log: %s", agent_log_file)

    try:
        # Use thread-safe context var instead of os.chdir() so parallel
        # LLM agents each get their own isolated repo path.
        with repo_path_context(repo_path.resolve()):
            # Get system-level instructions (from Langfuse SYSTEM prompt).
            # Only include the memory section if memory tools are provided.
            memory_tool_names = {t.name for t in MEMORY_TOOLS}
            has_memory_tools = any(t.name in memory_tool_names for t in tools)
            system_message = get_system_message(include_memory=has_memory_tools)

            # Combine instructions and task-specific content.
            # We only use a SystemMessage if tools are provided, as ReAct agents
            # benefit from it, while simple LLM calls are more direct with just a HumanMessage.
            prompt_parts = []
            if tools:
                prompt_parts.append(system_message)

            prompt_parts.append("# TASK DESCRIPTION")
            prompt_parts.append(content)

            combined_prompt = "\n\n".join(prompt_parts)

            messages = [
                SystemMessage(content=combined_prompt),
                HumanMessage(content="Begin.")
            ]

            # Combine per-agent handler with any user-provided callbacks
            all_callbacks = [agent_handler]
            if callbacks:
                all_callbacks.extend(callbacks)

            # Run the reactive agent
            result = _executor.invoke(
                {"messages": messages},
                config={
                    "recursion_limit": recursion_limit,
                    "callbacks": all_callbacks,
                    "run_name": run_name,
                    "configurable": {"thread_id": f"{thread_id}_{safe_run_name}"},
                }
            )

            # Extract the final answer from the last message
            messages = result.get("messages", [])
            if not messages:
                raise RuntimeError(" execution completed but returned no messages in state")

            raw_content = messages[-1].content
            if isinstance(raw_content, list):
                # Handle list of content blocks (common in Gemini 3)
                output = ""
                for block in raw_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        output += block.get("text", "")
                    elif isinstance(block, str):
                        output += block
                output = output.strip()
            else:
                output = raw_content.strip()

        # Extract JSON from potential conversational wrapper
        return parse_llm_json(output)

    except Exception as e:
        if isinstance(e, RetriableError):
            raise e
        logger.error("Error executing task with LangChain: %s", e)
        raise RuntimeError(f"LLM execution failed: {e}")
