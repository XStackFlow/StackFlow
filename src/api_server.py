import asyncio
import sys

# Windows: psycopg async requires SelectorEventLoop, not the default ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from contextlib import asynccontextmanager
import importlib
import logging
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from typing import Dict, Any, List, Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from src.utils.setup.env_utils import load_env

from langfuse import Langfuse, observe
import hashlib
import json

from src.graphs.graph_factory import build_langgraph_from_json, extract_edges, extract_interrupts, extract_all_node_ids
from src.utils.exceptions import ConfigurationError
from src.utils.setup.const import GRAPH_SAVE_PATH, PROJECT_ROOT
from src.utils.setup.env_utils import read_env_file, write_env_var
from src.utils.setup.logger import get_logger, get_thread_logs, get_persistent_session_logs
from src.utils.setup.db import create_checkpointer
from src.utils.ns_resolver import resolve_checkpoint_ns

logger = get_logger(__name__)

# Ensure custom graphs directory exists
GRAPH_SAVE_PATH.mkdir(parents=True, exist_ok=True)
MODULES_DIR = PROJECT_ROOT / "modules"


def load_config():
    """Load and validate environment configuration."""
    # Required environment variables
    # Only check non-DB ones here as DB ones are checked in get_conn_string
    required_configs = [
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
    ]

    # Optional environment variables
    optional_configs = [
        "RECURSION_LIMIT",
        "MAX_CONCURRENCY",
    ]

    # Build config map and validate required configs
    config = {}
    missing_config = []

    for key in required_configs:
        value = os.getenv(key)
        if not value:
            missing_config.append(key)
        config[key] = value

    for key in optional_configs:
        config[key] = os.getenv(key)

    if missing_config:
        raise ConfigurationError(f"Missing required environment variables: {', '.join(missing_config)}")

    return config


def setup_langfuse(config):
    """Initialize Langfuse client."""
    langfuse = Langfuse(
        public_key=config["LANGFUSE_PUBLIC_KEY"],
        secret_key=config["LANGFUSE_SECRET_KEY"],
        host=config["LANGFUSE_HOST"],
    )
    logger.info("Langfuse observability enabled")
    return langfuse


# Global configuration and clients
load_env()
config = load_config()
langfuse = setup_langfuse(config)

# Import the node registry helper
from src.utils.setup.node_registry import get_node_registry, invalidate_node_registry, get_load_errors, get_node_metadata

# ──────────────────────────────────────────────────────────────────────
# LANGGRAPH BUG FIX: Stale channel versions cause permanent interrupt deadlock
#
# LangGraph's PregelLoop._first() marks channel versions as "seen by interrupt"
# by iterating self.channels (the current compiled graph's channels). But the
# checkpoint's channel_versions may contain entries from nodes that were removed
# from the graph after the checkpoint was created. These orphaned entries have
# version numbers > the interrupt-seen version, so should_interrupt() always
# returns True, making it impossible to resume past any interrupt_before node.
#
# Fix: iterate checkpoint["channel_versions"] instead of self.channels, so
# ALL versions (including orphaned ones) are marked as "seen".
# ──────────────────────────────────────────────────────────────────────
def _patch_langgraph_resume():
    from langgraph.pregel._loop import PregelLoop
    from langgraph._internal._constants import INTERRUPT

    _original_first = PregelLoop._first

    def _patched_first(self, *, input_keys, updated_channels):
        result = _original_first(self, input_keys=input_keys, updated_channels=updated_channels)

        # After _first sets versions_seen[INTERRUPT] from self.channels,
        # force-sync any channel_versions entries that are NOT in self.channels
        # (orphaned from removed nodes). These stale entries have versions that
        # are permanently higher than interrupt_seen, causing should_interrupt
        # to always return True.
        interrupt_seen = self.checkpoint.get("versions_seen", {}).get(INTERRUPT)
        if interrupt_seen is not None:
            for k, v in self.checkpoint.get("channel_versions", {}).items():
                if k not in self.channels:
                    interrupt_seen[k] = v

        return result

    PregelLoop._first = _patched_first
    logger.info("Patched LangGraph PregelLoop._first to handle stale channel versions")

_patch_langgraph_resume()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run initialization tasks on startup."""
    # Build node registry (evicts stale module caches and re-imports fresh)
    get_node_registry()

    # Register module routes AFTER node registry so endpoint closures capture
    # the same module instances that nodes use at runtime.
    from src.utils.setup.module_registry import run_module_route_registrations
    run_module_route_registrations(app)

    # Run startup hooks for all installed modules (e.g. memory sync for llm)
    from src.utils.setup.module_registry import run_module_startup_hooks
    run_module_startup_hooks()

    # Sync prompts to Langfuse
    try:
        from src.utils.setup.langfuse_helper import register_prompts
        register_prompts(langfuse)
    except Exception as e:
        logger.error("Failed to register prompts to Langfuse on startup: %s", e)

    async with create_checkpointer() as checkpointer:
        app.state.checkpointer = checkpointer
        logger.info("Checkpointer ready (postgres)")

        # Auto-start graphs configured via AUTO_START_GRAPHS env var.
        # Format: comma-separated "graph_id:session_id" entries.
        # Example: AUTO_START_GRAPHS=slack/slack_assistant:slack
        auto_start_env = os.getenv("AUTO_START_GRAPHS", "").strip()
        if auto_start_env:
            for entry in auto_start_env.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                graph_id, _, session_id = entry.partition(":")
                if not session_id:
                    session_id = "default"
                thread_id = f"{graph_id}_{session_id}"
                graph_file = GRAPH_SAVE_PATH / f"{graph_id}.json"
                if not graph_file.exists():
                    logger.error("AUTO_START_GRAPHS: graph file not found for '%s'", graph_id)
                    continue
                with open(graph_file, "r", encoding="utf-8") as f:
                    graph_json = json.load(f)
                initial_params = {}
                extra = graph_json.get("extra", {})
                if "initial_state" in extra:
                    try:
                        initial_params = json.loads(extra["initial_state"])
                    except Exception:
                        pass
                active_tasks[thread_id] = {
                    "status": "running",
                    "task": None,
                    "active_nodes": set(),
                    "node_timers": {},
                    "debug_mode": False,
                }
                task = asyncio.ensure_future(run_graph_task(initial_params, thread_id, graph_json))
                active_tasks[thread_id]["task"] = task
                logger.info("Auto-started graph '%s' with thread_id '%s'", graph_id, thread_id)

        yield  # Wait for the application to shut down

app = FastAPI(title="StackFlow Graph API", lifespan=lifespan)

# Enable CORS for the LiteGraph editor
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Module route registration is done inside lifespan (after node registry builds)
# to ensure module instances are consistent. See lifespan() below.

# In-memory store for active graph executions
# thread_id -> { "status": "running/interrupted/completed", "current_node": "name", "result": {} }
# thread_id -> { "status": "running/interrupted/completed", "current_node": "name", "result": {} }
# thread_id -> { "status": "running/interrupted/completed", "current_node": "name", "result": {} }
active_tasks: Dict[str, Dict[str, Any]] = {}


class ExecutionRequest(BaseModel):
    root_graph_id: Optional[str] = None
    thread_id: str
    params: Optional[Dict[str, Any]] = None
    debug_mode: bool = False

class SeedStateRequest(BaseModel):
    thread_id: str
    root_graph_id: str
    current_graph_id: str = ""  # No longer used, kept for backwards compat
    checkpoint_ns: str = ""
    values: Dict[str, Any]
    as_node: str

@app.post("/save_graph/{graph_id:path}")
async def save_graph(graph_id: str, graph_data: Dict[str, Any]):
    """Save the LiteGraph JSON structure to disk. Supports subdirectory paths."""
    file_path = (GRAPH_SAVE_PATH / graph_id).resolve()
    if not str(file_path).startswith(str(GRAPH_SAVE_PATH.resolve())):
        raise HTTPException(status_code=400, detail="Invalid graph path")
    if not file_path.suffix:
        file_path = file_path.with_suffix(".json")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w") as f:
        json.dump(graph_data, f, indent=2)
    return {"message": "Graph saved", "path": file_path.relative_to(GRAPH_SAVE_PATH).as_posix()}

@app.get("/get_graph/{graph_id:path}")
async def get_graph(graph_id: str):
    """Load a previously saved graph structure. Supports subdirectory paths."""
    file_path = (GRAPH_SAVE_PATH / graph_id).resolve()
    if not str(file_path).startswith(str(GRAPH_SAVE_PATH.resolve())):
        raise HTTPException(status_code=400, detail="Invalid graph path")
    if not file_path.exists() and not graph_id.endswith(".json"):
        file_path = (GRAPH_SAVE_PATH / f"{graph_id}.json").resolve()
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Graph not found")
    with open(file_path, "r") as f:
        return json.load(f)

@app.delete("/delete_graph/{graph_id:path}")
async def delete_graph(graph_id: str):
    """Delete a graph file. Removes the parent folder if it becomes empty."""
    file_path = (GRAPH_SAVE_PATH / graph_id).resolve()
    if not str(file_path).startswith(str(GRAPH_SAVE_PATH.resolve())):
        raise HTTPException(status_code=400, detail="Invalid graph path")
    if not file_path.exists() and not graph_id.endswith(".json"):
        file_path = (GRAPH_SAVE_PATH / f"{graph_id}.json").resolve()
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Graph not found")
    file_path.unlink()
    parent = file_path.parent
    if parent != GRAPH_SAVE_PATH.resolve() and not any(parent.iterdir()):
        parent.rmdir()
    return {"message": "Deleted", "path": graph_id}


async def run_graph_task(params: Dict[str, Any], thread_id: str, graph_json: Dict[str, Any], debug_mode: bool = False):
    from src.utils.setup.logger import thread_id_scope
    import asyncio
    from langfuse import get_client
    
    # Extract graph name from thread_id (e.g. "PRFixer_xu9od0gt" -> "PRFixer")
    parts = thread_id.split("_")
    graph_name = "_".join(parts[:-1]) if len(parts) > 1 else thread_id
    
    # Create a Langfuse observation with the graph name
    langfuse_client = get_client()
    
    with langfuse_client.start_as_current_observation(
        name=f"{graph_name} Execution",
        as_type="span",
        input=params
    ) as observation:
        # Update the trace name to just the graph name
        observation.update_trace(name=graph_name)
        
        with thread_id_scope(thread_id):
            try:
                # Wipe previous agent logs for this session only
                import shutil
                agent_log_dir = PROJECT_ROOT / "logs" / "graph" / thread_id / "agents"
                if agent_log_dir.exists():
                    shutil.rmtree(agent_log_dir)
                    logger.info("Cleared previous agent logs: %s", agent_log_dir)
                
                checkpointer = getattr(app.state, 'checkpointer', None)
                
                # Use a helper to run the graph with either the shared or a local checkpointer
                async def run_with_cp(cp):
                    # Use the node registry initialized on demand
                    registry = get_node_registry()

                    workflow = build_langgraph_from_json(graph_json, registry, graph_id=graph_name)
                    interrupts = extract_interrupts(graph_json)
                    if debug_mode:
                        all_ids = extract_all_node_ids(graph_json)
                        interrupts = list(dict.fromkeys(all_ids + interrupts))
                    graph_runnable = workflow.compile(checkpointer=cp, interrupt_before=interrupts)

                    # Attach Langfuse LangChain callback for trace nesting
                    from langfuse.langchain import CallbackHandler
                    from opentelemetry import trace

                    current_span = trace.get_current_span()
                    trace_context = None
                    if current_span and current_span.is_recording():
                        span_context = current_span.get_span_context()
                        if span_context.is_valid:
                            trace_context = {
                                "trace_id": format(span_context.trace_id, '032x'),
                                "parent_span_id": format(span_context.span_id, '016x'),
                            }
                    callbacks = [CallbackHandler(trace_context=trace_context, update_trace=True)]

                    config_checkpoint = {
                        "configurable": {"thread_id": thread_id},
                        "recursion_limit": int(config.get("RECURSION_LIMIT") or 25),
                        "max_concurrency": int(config.get("MAX_CONCURRENCY") or 5),
                        "callbacks": callbacks,
                    }
                    logger.info("Config Checkpoint: recursion_limit=%d, max_concurrency=%d", 
                                        config_checkpoint["recursion_limit"], config_checkpoint["max_concurrency"])
                    
                    existing_state = await graph_runnable.aget_state({"configurable": {"thread_id": thread_id}})
                    
                    # Check if we should start fresh or resume
                    # We resume if there are values in the checkpoint (either from real run or seeding)
                    input_data = {**params, "thread_id": thread_id} if not existing_state.values else None
                    
                    if input_data is None:
                        logger.info("Resuming execution for thread %s from checkpoint (recursion_limit: %d).", thread_id, config_checkpoint['recursion_limit'])
                    else:
                        logger.info("Starting NEW execution for thread %s (recursion_limit: %d).", thread_id, config_checkpoint['recursion_limit'])
                    
                    # Use astream_events to get real-time node start/end events
                    async for event in graph_runnable.astream_events(input_data, config=config_checkpoint, version="v2"):
                        kind = event.get("event")
                        name = event.get("name")
                        metadata = event.get("metadata", {})
                        
                        # Detect high-level start/end events (nodes and chains)
                        is_start = kind in ["on_node_start", "on_chain_start"]
                        is_end = kind in ["on_node_end", "on_chain_end"]

                        # Improved Identification:
                        # langgraph_checkpoint_ns reports the node's OWN namespace (e.g. "DelayNode_3:uuid")
                        # But for matching we need the PARENT graph's namespace.
                        # Root nodes: ns="NodeName:uuid" → parent_ns="" 
                        # Nested:     ns="SubA:uuid|NodeB:uuid" → parent_ns="SubA:uuid"
                        ns = metadata.get("langgraph_checkpoint_ns", "")
                        # Extract parent namespace: strip the last |segment (the node's own entry)
                        if "|" in ns:
                            parent_ns = ns.rsplit("|", 1)[0]
                        else:
                            parent_ns = ""  # Node is at root level
                        node_name = metadata.get("langgraph_node") or name
                        full_key = f"{parent_ns}:::{node_name}"
                        triggers = metadata.get("langgraph_triggers", [])

                        # Standardize node_name to match frontend expectation (sanitized unique_id)
                        # The 'name' from astream_events for a node is often its unique ID in the graph.
                        
                        if node_name and node_name not in ["__start__", "__end__", "LangGraph", "workflow"]:
                            if is_start:
                                logger.info(">>> START [%s]: %s (ns: %s)", kind, node_name, ns)
                                
                                # PROACTIVE GHOSTING FIX (Namespace Aware):
                                if thread_id not in active_tasks: continue
                                current_active = active_tasks[thread_id].get("active_nodes", set())
                                if triggers:
                                    for t in triggers:
                                        # Clear previous nodes in the SAME namespace
                                        trigger_prefix = f"{parent_ns}:::{t}"
                                        # Match exact or namespaced triggers
                                        targets = [a for a in current_active if a == trigger_prefix or a.startswith(trigger_prefix + ":")]
                                        for target in targets:
                                            logger.info("Proactively cleared ghost node: %s (triggered %s)", target, node_name)
                                            current_active.discard(target)

                                if thread_id in active_tasks:
                                    active_tasks[thread_id]["active_nodes"].add(full_key)
                            
                            if is_end:
                                logger.info("<<< NODE END [%s]: %s (ns: %s)", kind, node_name, ns)
                                # Record completion time for green pulse
                                if thread_id in active_tasks:
                                    active_tasks[thread_id].setdefault("node_timers", {})[full_key] = time.time()
                                    active_tasks[thread_id]["active_nodes"].discard(full_key)

                        # Check for stop signal (polled during events)
                        if thread_id not in active_tasks or active_tasks[thread_id].get("stop_requested"):
                            logger.warning("Stop requested or state cleared for %s", thread_id)
                            if thread_id in active_tasks:
                                active_tasks[thread_id]["status"] = "interrupted"
                            try:
                                langfuse.flush()
                            except: pass
                            return

                def _get_interrupts():
                    base = extract_interrupts(graph_json)
                    if debug_mode:
                        all_ids = extract_all_node_ids(graph_json)
                        return list(dict.fromkeys(all_ids + base))
                    return base

                if not checkpointer:
                    async with create_checkpointer() as cp:
                        await run_with_cp(cp)
                        # Check if graph is truly done or just interrupted
                        post_state = await (build_langgraph_from_json(graph_json, get_node_registry(), graph_id=graph_name)
                                            .compile(checkpointer=cp, interrupt_before=_get_interrupts())
                                            .aget_state({"configurable": {"thread_id": thread_id}}))
                        is_interrupted = bool(post_state.next)
                else:
                    await run_with_cp(checkpointer)
                    post_state = await (build_langgraph_from_json(graph_json, get_node_registry(), graph_id=graph_name)
                                        .compile(checkpointer=checkpointer, interrupt_before=_get_interrupts())
                                        .aget_state({"configurable": {"thread_id": thread_id}}))
                    is_interrupted = bool(post_state.next)

                if is_interrupted:
                    logger.info("Graph execution paused at interrupt for thread %s (next: %s)", thread_id, post_state.next)
                    if thread_id in active_tasks:
                        active_tasks[thread_id]["status"] = "interrupted"
                        active_tasks[thread_id]["active_nodes"] = set()
                    observation.update(output={"status": "interrupted", "next": list(post_state.next)})
                else:
                    logger.info("Graph execution completed successfully for thread %s", thread_id)
                    if thread_id in active_tasks:
                        active_tasks[thread_id]["status"] = "completed"
                        active_tasks[thread_id]["active_nodes"] = set()
                        active_tasks[thread_id]["ended_at"] = time.time()
                    observation.update(output={"status": "completed"})
                
                # Flush Langfuse traces
                try:
                    langfuse.flush()
                except Exception as e:
                    logger.warning("Failed to flush Langfuse traces: %s", e)
                    
            except asyncio.CancelledError:
                # Task was interrupted via stop endpoint
                logger.warning("Graph execution interrupted for thread %s", thread_id)
                if thread_id in active_tasks:
                    active_tasks[thread_id].update({
                        "status": "interrupted",
                        "active_nodes": set()
                    })
                # Update observation with interruption
                observation.update(output={"status": "interrupted"}, level="WARNING", status_message="Execution interrupted by user")
                # Flush Langfuse traces
                try:
                    langfuse.flush()
                except: pass
                raise  # Re-raise to properly complete the cancellation
                    
            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                logger.error("Graph execution failed for thread %s:\n%s", thread_id, error_trace)
                if thread_id in active_tasks:
                    active_tasks[thread_id].update({
                        "status": "failed",
                        "error": str(e) or "Unknown error",
                        "traceback": error_trace,
                        "ended_at": time.time()
                    })
                else:
                    active_tasks[thread_id] = {"status": "failed", "error": str(e) or "Unknown error", "traceback": error_trace, "ended_at": time.time()}
                # Update observation with error
                observation.update(output={"status": "failed", "error": str(e)}, level="ERROR", status_message=str(e))


@app.post("/execute")
async def execute_graph(request: ExecutionRequest):
    """Unified endpoint for executing graphs. Always reads latest from disk to avoid discrepancies."""
    thread_id = request.thread_id
    
    # 1. Resolve which graph to load
    # Use root_graph_id if provided, otherwise derive it from thread_id
    effective_root_id = request.root_graph_id
    if not effective_root_id:
        parts = thread_id.split("_")
        effective_root_id = "_".join(parts[:-1]) if len(parts) > 1 else thread_id
    
    # 2. Load from disk (Source of Truth)
    # We always load from disk to ensure consistency.
    file_path = GRAPH_SAVE_PATH / f"{effective_root_id}.json"
    if not file_path.exists():
         return {"status": "error", "message": f"Graph definition '{effective_root_id}' not found on disk."}
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            graph_json = json.load(f)
        logger.info("Loaded graph '%s' from disk for execution", effective_root_id)
    except Exception as e:
        logger.error("Failed to load graph %s from disk: %s", effective_root_id, e)
        return {"status": "error", "message": f"Failed to load graph from disk: {e}"}

    # Merge default params from graph_json with request.params
    initial_params = {}
    try:
        extra = graph_json.get("extra", {})
        if "initial_state" in extra:
            initial_params = json.loads(extra["initial_state"])
    except Exception as e:
        logger.warning("Failed to parse initial_state from graph JSON: %s", e)

    params = {**initial_params, **(request.params or {})}

    if thread_id in active_tasks and active_tasks[thread_id]["status"] == "running":
        return {"message": "Graph already running", "thread_id": thread_id}

    # Validate that the graph can be built before starting async execution
    try:
        registry = get_node_registry()
        workflow = build_langgraph_from_json(graph_json, registry, graph_id="execute_request_root")
        logger.info("Graph validation successful for thread %s", thread_id)
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        logger.error("Graph validation failed for thread %s: %s", thread_id, e)
        return {
            "status": "error",
            "message": f"Failed to build graph: {str(e)}",
            "traceback": error_trace
        }

    # Initialize active_tasks entry first
    active_tasks[thread_id] = {
        "status": "running",
        "task": None,
        "active_nodes": set(),
        "node_timers": {},
        "debug_mode": request.debug_mode,
    }

    # Create the task using ensure_future to properly schedule it
    task = asyncio.ensure_future(run_graph_task(params, thread_id, graph_json, debug_mode=request.debug_mode))
    
    # Store task reference for cancellation
    active_tasks[thread_id]["task"] = task
    
    # Add cleanup callback when task completes
    def cleanup_task(fut):
        if thread_id in active_tasks and active_tasks[thread_id].get("task") == fut:
            # Remove task reference but keep status info
            active_tasks[thread_id].pop("task", None)
    
    task.add_done_callback(cleanup_task)
    
    return {"message": "Execution started", "thread_id": thread_id}



async def collect_all_next_tasks(graph_runnable, thread_id: str, current_ns: str = "") -> list[str]:
    """Recursively discover all pending tasks across all namespaces."""
    lookup_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": current_ns}}
    try:
        snapshot = await graph_runnable.aget_state(lookup_config)
    except Exception:
        return []
    
    tasks = []
    # In LangGraph, tasks are the units of execution.
    # If a task is a subgraph, its 'state' will contain the checkpoint_ns for that subgraph.
    if snapshot.tasks:
        for task in snapshot.tasks:
            node_name = task.name
            task_ns = task.state.get("configurable", {}).get("checkpoint_ns") if task.state else None
            
            # If it has a different namespace, it's a nested call
            if task_ns and task_ns != current_ns:
                sub_tasks = await collect_all_next_tasks(graph_runnable, thread_id, task_ns)
                if sub_tasks:
                    # Prefix with parent node name using '@@' separator
                    tasks.extend([f"{node_name}@@{st}" for st in sub_tasks])
                else:
                    # It's an empty/finished subgraph task that hasn't cleared yet
                    tasks.append(node_name)
            else:
                tasks.append(node_name)
                
    return tasks


@app.get("/graph_status/{thread_id:path}")
async def get_status(thread_id: str, subgraph_node: Optional[str] = None):
    """Retrieve current state and active tasks for a graph execution."""
    try:
        # Derive root graph from thread_id (format: graphName_sessionId)
        parts = thread_id.rsplit("_", 1)
        root_graph_id = parts[0] if len(parts) > 1 else thread_id
        
        file_path = GRAPH_SAVE_PATH / f"{root_graph_id}.json"
        if not file_path.exists():
            return {"status": "not_found", "message": f"Graph definition for {root_graph_id} not found."}
        
        with open(file_path, "r", encoding="utf-8") as f:
            graph_json = json.load(f)
        
        checkpointer = getattr(app.state, 'checkpointer', None)
        
        async def run_get_status(cp):
            workflow = build_langgraph_from_json(graph_json, get_node_registry(), graph_id=root_graph_id)
            interrupts = extract_interrupts(graph_json)
            graph_runnable = workflow.compile(checkpointer=cp, interrupt_before=interrupts)

            # COORDINATES for the main execution
            config = {"configurable": {"thread_id": thread_id}}
            
            try:
                # 1. DISCOVERY: Resolve logical subgraph_node path to real checkpoint_ns
                target_ns, residual_prefix, resolved_all = await resolve_checkpoint_ns(graph_runnable, thread_id, subgraph_node)
                
                logger.info("[DEBUG_STATUS] thread=%s, path=%s -> resolved_ns='%s', residual_pfx='%s', resolved_all=%s", 
                                     thread_id, subgraph_node, target_ns, residual_prefix, resolved_all)
                
                is_subgraph_fallback = False
                if subgraph_node and not resolved_all:
                    logger.warning("[DEBUG_STATUS] FAILED TO RESOLVE PATH: %s", subgraph_node)
                    # Even if not fully resolved, let's try to fetch root state as fallback
                    # instead of returning empty, so the UI at least shows something.
                    target_ns = ""
                    residual_prefix = ""
                    is_subgraph_fallback = True
                
                # 2. FETCH STATE: Load current snapshot for the resolved namespace
                config["configurable"]["checkpoint_ns"] = target_ns
                state_snapshot = await graph_runnable.aget_state(config)
                
                logger.info("[DEBUG_STATUS] Snapshot metadata: %s, Next: %s, Tasks: %d", 
                            state_snapshot.metadata, state_snapshot.next, len(state_snapshot.tasks))
                
                # If the target NS is empty and has no state, check root (fallback)
                if not target_ns and not state_snapshot.values:
                    state_snapshot = await graph_runnable.aget_state({"configurable": {"thread_id": thread_id}})
                
                vals = state_snapshot.values or {}
                
                # IMPORTANT: For namespaced views (subgraphs), we must merge the root state
                # so the UI display is "complete" and reflects what nodes actually see.
                if target_ns:
                    root_snapshot = await graph_runnable.aget_state({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
                    root_values = root_snapshot.values or {}
                    # Merge: Namespace state overrides root state, similar to BaseNode.run()
                    merged_values = {**root_values}
                    for k, v in vals.items():
                        merged_values[k] = v
                    raw_values = merged_values
                else:
                    raw_values = vals
            
            except Exception as e:
                logger.error("STATE FETCH ERROR: %s", e)
                return {"status": "error", "message": f"Checkpoint lookup failed: {str(e)}"}

            if not raw_values and thread_id not in active_tasks:
                return {"status": "idle", "thread_id": thread_id}

            # Extract pending nodes (discovery)
            all_next_nodes = await collect_all_next_tasks(graph_runnable, thread_id, "")
            next_nodes_inner = list(state_snapshot.next) if state_snapshot.next else []
            
            # For non-inline subgraphs (target_ns is set, residual_prefix is empty),
            # also collect tasks from within the subgraph's namespace.
            # This gives us node names relative to the subgraph view.
            inner_next_nodes = []
            if target_ns and not residual_prefix:
                inner_next_nodes = await collect_all_next_tasks(graph_runnable, thread_id, target_ns)
                logger.debug("SUBGRAPH NEXT_NODES: target_ns='%s', inner_next_nodes=%s, next_nodes_inner=%s",
                           target_ns, inner_next_nodes, next_nodes_inner)
            
            # Recursive unwrapping: merge __root__, filter tombstones, flatten namespaces
            def recursive_unwrap(obj, merge_to_root=True, depth=0):
                if not isinstance(obj, dict):
                    return obj
                work_data = {}
                if "__root__" in obj and isinstance(obj["__root__"], dict):
                    work_data.update(obj["__root__"])
                for k, v in obj.items():
                    if k != "__root__":
                        work_data[k] = v
                res = {}
                namespaces = {}
                for k, v in work_data.items():
                    if v is None:
                        continue
                    if isinstance(k, str) and k.startswith("__") and k != "__subgraph_node__":
                        continue
                    if isinstance(k, str) and k.endswith("@@namespace"):
                        namespaces[k] = v
                    else:
                        if isinstance(v, dict):
                            res[k] = recursive_unwrap(v, merge_to_root=merge_to_root, depth=depth+1)
                        elif isinstance(v, list):
                            res[k] = [recursive_unwrap(i, merge_to_root=merge_to_root, depth=depth+1) if isinstance(i, dict) else i for i in v]
                        else:
                            res[k] = v
                return res

            extracted_db_state = recursive_unwrap(raw_values) if raw_values else {}
            
            # For inline subgraphs (residual_prefix is set), show the full root state
            # as-is since inline subgraphs share the root checkpoint.
            if residual_prefix and extracted_db_state:
                logger.debug("STATE SCOPE: inline subgraph — showing full root state")
            
            if subgraph_node:
                extracted_db_state["__subgraph_node__"] = subgraph_node

            # 3. LIVE STATUS: Merge persistent state with in-memory execution data
            if thread_id in active_tasks:
                task_info = {k: v for k, v in active_tasks[thread_id].items() if k != "task"}
                task_info["last_state"] = extracted_db_state
                task_info["result"] = extracted_db_state
                # Filter next_nodes by residual_prefix so subgraph views show the correct nodes
                # Use all_next_nodes (recursive) to ensure inlined nodes are captured
                filtered_next = []
                # When we're in a subgraph fallback (resolution failed), don't leak
                # root next_nodes — they'd glow subgraph nodes with matching IDs.
                if is_subgraph_fallback:
                    pass  # keep filtered_next empty
                else:
                    # Only use inner_next_nodes for subgraph scope — never fall back to root-level
                    # next_nodes_inner, which would leak parent-graph node names into the subgraph view.
                    scope_local_next = inner_next_nodes
                    if target_ns and not residual_prefix and scope_local_next:
                        # Non-inline subgraph: use scope-local nodes directly (already scoped to subgraph ns)
                        for nn in scope_local_next:
                            if not (nn.startswith("START_") or nn.startswith("END_") or "@@START_" in nn or "@@END_" in nn):
                                if "@@" in nn:
                                    filtered_next.append(nn.split("@@")[0])
                                else:
                                    filtered_next.append(nn)
                    else:
                        for nn in all_next_nodes:
                            if residual_prefix:
                                if nn.startswith(residual_prefix + "@@"):
                                    rel = nn[len(residual_prefix)+2:]
                                    if not (rel.startswith("START_") or rel.startswith("END_") or "@@START_" in rel or "@@END_" in rel):
                                        if "@@" in rel:
                                            filtered_next.append(rel.split("@@")[0])
                                        else:
                                            filtered_next.append(rel)
                                elif nn == residual_prefix:
                                    filtered_next.append(nn)
                            elif not target_ns:
                                # Root view only — skip if we're in a subgraph scope that hasn't been entered
                                if "@@" in nn:
                                    filtered_next.append(nn.split("@@")[0])
                                else:
                                    filtered_next.append(nn)

                task_info["next_nodes"] = filtered_next
                task_info["viewing_subgraph"] = bool(subgraph_node)
                task_info["next_nodes_raw"] = next_nodes_inner
                task_info["new_logs"] = get_thread_logs(thread_id)
                
                # Filter active_nodes and node_elapsed by current target_ns and residual_prefix
                # When in subgraph fallback, skip entirely to avoid parent data leaking
                all_active = active_tasks[thread_id].get("active_nodes", set()) if not is_subgraph_fallback else set()
                task_info["active_nodes"] = []

                for k in all_active:
                    if ":::" not in k: continue
                    ns_part, name_part = k.split(":::", 1)
                    
                    # Clean ns_part for comparison (Target is always stripped of IDs in our resolver)
                    ns_segments = [s.split(":")[0] for s in ns_part.split("|")] if ns_part else []
                    target_segments = [s.split(":")[0] for s in target_ns.split("|")] if target_ns else []
                    
                    if ns_segments == target_segments:
                        # Correct namespace! Now check for inlined residual matches
                        if residual_prefix:
                            if name_part.startswith(residual_prefix + "@@"):
                                # Strip the prefix so the node glows in the current view
                                rel_name = name_part[len(residual_prefix)+2:]
                                if "@@" in rel_name:
                                    # Highlight the immediate child container
                                    task_info["active_nodes"].append(rel_name.split("@@")[0])
                                else:
                                    task_info["active_nodes"].append(rel_name)
                            elif name_part == residual_prefix:
                                # The container itself is running
                                task_info["active_nodes"].append(name_part)
                        else:
                            # We are at the root of this namespace: highlight nodes
                            if "@@" in name_part:
                                # Highlight the immediate parent for inlined nodes
                                task_info["active_nodes"].append(name_part.split("@@")[0])
                            else:
                                task_info["active_nodes"].append(name_part)
                                
                    elif len(ns_segments) > len(target_segments) and ns_segments[:len(target_segments)] == target_segments:
                        # Path Match: Node is deep inside a nested subgraph of current view
                        sub_node_id = ns_segments[len(target_segments)].split("@@")[0]
                        task_info["active_nodes"].append(sub_node_id)

                # 4. RECENT COMPLETION TIMERS (Green Pulse)
                # When in subgraph fallback, skip to avoid parent green pulses leaking
                now = time.time()
                all_timers = active_tasks[thread_id].get("node_timers", {}) if not is_subgraph_fallback else {}
                task_info["node_elapsed"] = {}
                
                # DEBUG: Log raw timer state
                if all_timers:
                    logger.debug("GREEN PULSE DEBUG: %d total timers, target_ns='%s', residual_prefix='%s'", 
                               len(all_timers), target_ns, residual_prefix)
                    for tk, tv in all_timers.items():
                        logger.debug("  TIMER: key='%s', age=%.1fs", tk, now - tv)
                
                # 5-second window: must survive at least one polling cycle (1s) + network latency
                recent_timers = {k: ts for k, ts in all_timers.items() if (now - ts) < 5.0}
                
                if all_timers and not recent_timers:
                    logger.debug("GREEN PULSE: All %d timers are older than 5s, none will pulse", len(all_timers))

                for k, end_time in recent_timers.items():
                    if ":::" not in k: continue
                    ns_part, name_part = k.split(":::", 1)
                    ns_segments = [s.split(":")[0] for s in ns_part.split("|")] if ns_part else []
                    target_segments = [s.split(":")[0] for s in target_ns.split("|")] if target_ns else []

                    logger.debug("GREEN PULSE MATCH: key='%s', ns_segments=%s, target_segments=%s, name_part='%s'",
                               k, ns_segments, target_segments, name_part)

                    if ns_segments == target_segments:
                        if residual_prefix:
                            if name_part.startswith(residual_prefix + "@@"):
                                # Internal node of current inline view just finished
                                rel_name = name_part[len(residual_prefix)+2:]
                                # Attribute to the immediate child visible in this view
                                target_node = rel_name.split("@@")[0]
                                task_info["node_elapsed"][target_node] = end_time
                                logger.debug("GREEN PULSE HIT (inlined): '%s' -> pulse '%s'", rel_name, target_node)
                            elif name_part == residual_prefix:
                                # The container itself just finished
                                task_info["node_elapsed"][name_part] = end_time
                                logger.debug("GREEN PULSE HIT (exact): '%s'", name_part)
                        else:
                            # Root view: Attribute to the immediate container node
                            target_node = name_part.split("@@")[0]
                            task_info["node_elapsed"][target_node] = end_time
                            logger.debug("GREEN PULSE HIT (root): '%s' -> pulse '%s'", name_part, target_node)
                    else:
                        logger.debug("GREEN PULSE MISS: ns mismatch ns_segments=%s != target_segments=%s", ns_segments, target_segments)
                
                logger.debug("GREEN PULSE RESULT: node_elapsed=%s", task_info["node_elapsed"])
                
                task_info["next_nodes_global"] = all_next_nodes
                return task_info
                
            # Idle/Completed/Interrupted Status
            status = "idle"
            if extracted_db_state:
                # Use all_next_nodes (recursive) to detect interrupts inside subgraphs
                status = "interrupted" if (next_nodes_inner or all_next_nodes) else "completed"
            
            # Filter next_nodes by residual_prefix so subgraph views show the correct nodes
            # Use all_next_nodes (recursive) to ensure inlined nodes are captured,
            # but exclude START/END nodes which are almost never meaningful interrupt points.
            filtered_next = []
            # When we're in a subgraph fallback (resolution failed), don't leak
            # root next_nodes — they'd glow subgraph nodes with matching IDs.
            if is_subgraph_fallback:
                pass  # keep filtered_next empty
            else:
                # Only use inner_next_nodes for subgraph scope — never fall back to root-level
                # next_nodes_inner, which would leak parent-graph node names into the subgraph view.
                scope_local_next = inner_next_nodes
                if target_ns and not residual_prefix and scope_local_next:
                    # Non-inline subgraph: use scope-local nodes directly (already scoped to subgraph ns)
                    for nn in scope_local_next:
                        if not (nn.startswith("START_") or nn.startswith("END_") or "@@START_" in nn or "@@END_" in nn):
                            if "@@" in nn:
                                filtered_next.append(nn.split("@@")[0])
                            else:
                                filtered_next.append(nn)
                else:
                    for nn in all_next_nodes:
                        if residual_prefix:
                            if nn.startswith(residual_prefix + "@@"):
                                rel = nn[len(residual_prefix)+2:]

                                # Only include if it's not a START/END node
                                if not (rel.startswith("START_") or rel.startswith("END_") or "@@START_" in rel or "@@END_" in rel):
                                    # Only the immediate child (no nested @@)
                                    if "@@" in rel:
                                        filtered_next.append(rel.split("@@")[0])
                                    else:
                                        filtered_next.append(rel)
                            elif nn == residual_prefix:
                                filtered_next.append(nn)
                        elif not target_ns:
                            # Root view only — skip if we're in a subgraph scope that hasn't been entered
                            if "@@" in nn:
                                filtered_next.append(nn.split("@@")[0])
                            else:
                                filtered_next.append(nn)

            logger.debug("STATUS RESULT: status=%s, next_nodes_inner=%s, all_next_nodes=%s, filtered_next=%s, residual_prefix='%s'",
                       status, next_nodes_inner, all_next_nodes, filtered_next, residual_prefix)

            return {
                "status": status, "thread_id": thread_id,
                "last_state": extracted_db_state, "result": extracted_db_state,
                "next_nodes": filtered_next, "next_nodes_global": all_next_nodes,
                "viewing_subgraph": bool(subgraph_node),
                "active_nodes": [], "node_elapsed": {}
            }

        # Execution context manager
        if not checkpointer:
            async with create_checkpointer() as cp:
                return await run_get_status(cp)
        else:
            return await run_get_status(checkpointer)

    except Exception as e:
        logger.error("Error fetching status for %s: %s", thread_id, e)
        import traceback
        logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}


@app.get("/logs/{thread_id:path}")
async def get_session_logs(thread_id: str, limit: int = 100):
    """Fetch the last N persistent logs for a specific session/thread."""
    from src.utils.setup.logger import get_persistent_session_logs
    logs = get_persistent_session_logs(thread_id, limit=limit)
    return {"thread_id": thread_id, "logs": logs, "limit": limit}

class SyncLogsRequest(BaseModel):
    active_session_ids: list[str]

class PostLogRequest(BaseModel):
    thread_id: str
    message: str
    level: str = "info"

@app.post("/post_log")
async def post_log(request: PostLogRequest):
    """Manually add a log entry for a specific thread from the frontend."""
    from src.utils.setup.logger import thread_id_scope
    with thread_id_scope(request.thread_id):
        if request.level == "error":
            logger.error(request.message)
        elif request.level == "warning":
            logger.warning(request.message)
        else:
            logger.info(request.message)
        
    return {"status": "success"}

@app.post("/session/sync_logs")
async def sync_session_logs(request: SyncLogsRequest):
    """Delete all log files belonging to sessions not in the active list."""
    from src.utils.setup.const import SESSION_LOGS_DIR
    deleted_count = 0
    if not SESSION_LOGS_DIR.exists():
        return {"message": "No logs directory found", "deleted_count": 0}
    
    # We expect filenames like "GraphName_SessionID.log"
    active_ids = set(request.active_session_ids)
    
    for log_file in SESSION_LOGS_DIR.glob("*.log"):
        # file.stem is the thread_id (e.g., "PRFixer_xu9od0gt")
        parts = log_file.stem.split("_")
        if not parts:
            continue
            
        session_id = parts[-1]
        if session_id not in active_ids:
            try:
                log_file.unlink()
                deleted_count += 1
            except Exception as e:
                logger.warning("Failed to delete orphaned log file %s: %s", log_file, e)
                
    return {"message": f"Sync complete. Deleted {deleted_count} orphaned logs.", "deleted_count": deleted_count}

@app.get("/active_sessions")
async def list_active_sessions():
    """List all currently running sessions, plus recently ended ones (within 10s) so the frontend can detect them."""
    active = []
    now = time.time()
    for tid, info in active_tasks.items():
        status = info.get("status")
        # Always include running/interrupted sessions
        if status in ["running", "interrupted"] and not info.get("stop_requested"):
            active.append({
                "thread_id": tid,
                "active_nodes": list(info.get("active_nodes", [])),
                "status": status
            })
        # Include recently ended (completed/failed) sessions for up to 10s so the frontend catches them
        elif status in ["completed", "failed"]:
            ended_at = info.get("ended_at")
            if ended_at and (now - ended_at) < 10:
                active.append({
                    "thread_id": tid,
                    "active_nodes": [],
                    "status": status
                })
    return {"active_sessions": active}

def _extract_output_keys(cls) -> list[str]:
    """Extract state keys from the return dict of a node's _run method via AST parsing."""
    import ast
    import inspect
    import textwrap
    try:
        source = inspect.getsource(cls._run)
        source = textwrap.dedent(source)
        tree = ast.parse(source)
        keys: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.add(key.value)
        return sorted(keys)
    except Exception:
        return []


@app.get("/list_nodes")
async def list_available_nodes():
    """List all available worker nodes for the LiteGraph editor."""
    registry = get_node_registry()
    node_metadata = get_node_metadata()
    available = []
    from src.nodes.abstract import RouterNode
    import inspect

    for name, obj in registry.items():
        # Determine category from module path
        # modules.aws.nodes.foo  -> "aws"
        # src.nodes.common.foo   -> "common"
        module_parts = obj.__module__.split('.')
        if module_parts[0] in ("modules", "installed"):
            category = module_parts[1]
        elif len(module_parts) > 2:
            category = module_parts[2]
        else:
            category = "other"
        
        # Inspect constructor to find properties
        properties = {}
        try:
            signature = inspect.signature(obj.__init__)
            for param_name, param in signature.parameters.items():
                if param_name in ["self", "args", "kwargs"] or param.kind in [param.VAR_KEYWORD, param.VAR_POSITIONAL]:
                    continue
                
                # Determine default value and type
                default = None
                if param.default is not inspect.Parameter.empty:
                    default = param.default
                
                # Extract inner type and metadata for Annotated and Optional[...]
                from typing import get_args, get_origin, Union, Annotated
                from src.inputs.standard_inputs import Resolvable
                annotation = param.annotation
                options = []
                link = None
                
                # Unwrap Resolvable[T] to get T
                is_resolvable = False
                if get_origin(annotation) is Resolvable or (isinstance(annotation, type) and issubclass(annotation, Resolvable)):
                    is_resolvable = True
                    inner_args = get_args(annotation)
                    if inner_args:
                        annotation = inner_args[0]
                
                # Unwrap Resolvable default values
                if isinstance(default, Resolvable):
                    default = default.value

                param_type = "string"
                # Handle Annotated (like Model or Prompt)
                is_json_type = False
                if get_origin(annotation) is Annotated:
                    args = get_args(annotation)
                    if args:
                        # actual_type = args[0]
                        for extra in args[1:]:
                            # Check for 'json_type' marker
                            if extra == "json_type":
                                is_json_type = True
                            # Check for 'slack_type' marker
                            elif extra == "slack_type":
                                param_type = "slack"
                            # Check for 'template_type' marker
                            elif extra == "template_type":
                                param_type = "template"
                            # Check for 'file_type' marker
                            elif extra == "file_type":
                                param_type = "file"
                            # Look for a list of strings (enum options)
                            elif isinstance(extra, list):
                                options = extra
                            # Look for a string starting with http (external link)
                            elif isinstance(extra, str) and extra.startswith("http"):
                                link = extra
                        annotation = args[0]

                # Handle Optional (Union[T, None])
                if get_origin(annotation) is Union:
                    args = get_args(annotation)
                    args = [a for a in args if a is not type(None)]
                    if args:
                        annotation = args[0]

                if is_json_type:
                    param_type = "json"
                elif options:
                    # Check if it's a list (for multi-select) or a single value (for enum)
                    if get_origin(annotation) is list or annotation is list:
                        param_type = "multi_select"
                    else:
                        param_type = "enum"
                elif not is_resolvable:
                    # Only use numeric/boolean widgets for non-resolvable fields.
                    # Resolvable fields stay as "string" so template strings can be entered.
                    if annotation is int:
                        param_type = "number"
                    elif annotation is float:
                        param_type = "number"
                    elif annotation is bool:
                        param_type = "boolean"
                    elif default is not None:
                        if isinstance(default, (int, float)) and not isinstance(default, bool):
                            param_type = "number"
                        elif isinstance(default, bool):
                            param_type = "boolean"
                
                properties[param_name] = {
                    "type": param_type,
                    "default": default,
                    "options": options,
                    "link": link
                }
        except Exception as e:
            logger.warning("Failed to inspect signature for %s: %s", name, e)

        is_router = issubclass(obj, RouterNode)
        options = []
        if is_router:
            try:
                # Instantiate temporarily to get options
                options = obj().get_route_options()
            except: pass

        output_keys = _extract_output_keys(obj)

        meta = node_metadata.get(name, {})
        available.append({
            "name": name,
            "category": category,
            "type": "router" if is_router else "standard",
            "route_options": options,
            "properties": properties,
            "output_keys": output_keys,
            "origin": meta.get("origin"),
            "source_url": meta.get("source_url"),
            "module_id": meta.get("module_id"),
        })
                
    return {"nodes": available}


@app.get("/browse_files")
async def browse_files(path: str = "", extensions: str = ""):
    """Browse server filesystem for file selection widgets.

    Args:
        path: Directory to list. Defaults to the project root.
        extensions: Comma-separated file extensions to filter (e.g. ".mp3,.wav,.flac").
    """
    from pathlib import Path as P
    base = P(path) if path else P.cwd()
    if not base.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {base}")
    if not base.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {base}")

    ext_filter = {e.strip().lower() for e in extensions.split(",") if e.strip()} if extensions else set()

    items = []
    try:
        for entry in sorted(base.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                items.append({"name": entry.name, "path": str(entry), "is_dir": True})
            elif not ext_filter or entry.suffix.lower() in ext_filter:
                items.append({"name": entry.name, "path": str(entry), "is_dir": False})
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {base}")

    return {"path": str(base), "parent": str(base.parent) if base.parent != base else None, "items": items}




@app.get("/modules/check-updates")
async def check_all_module_updates():
    """Batch check all external modules for available updates.

    Returns {module_id: {"update_available": bool, "local_sha": ..., "remote_sha": ...}}
    Runs ls-remote concurrently for all external modules (~1s total).
    """
    from src.utils.setup.module_registry import INSTALLED_DIR

    if not INSTALLED_DIR.exists():
        return {"updates": {}}

    # Gather all external modules with source URLs
    external_modules = {}
    for d in INSTALLED_DIR.iterdir():
        if not d.is_dir():
            continue
        url_file = d / ".source_url"
        sha_file = d / ".git_sha"
        if url_file.exists():
            external_modules[d.name] = {
                "raw_url": url_file.read_text().strip(),
                "local_sha": sha_file.read_text().strip() if sha_file.exists() else None,
            }

    if not external_modules:
        return {"updates": {}}

    loop = asyncio.get_event_loop()

    async def _check_one(module_id: str, info: dict):
        try:
            clone_url, _, branch = _parse_github_url(info["raw_url"])
            remote_sha = await loop.run_in_executor(None, _get_remote_sha, clone_url, branch)
            local_sha = info["local_sha"]
            return module_id, {
                "update_available": bool(local_sha and remote_sha and local_sha != remote_sha),
                "local_sha": local_sha[:7] if local_sha else None,
                "remote_sha": remote_sha[:7] if remote_sha else None,
            }
        except Exception as e:
            logger.debug("check-update for '%s' failed: %s", module_id, e)
            return module_id, {"update_available": False, "error": str(e)}

    results = await asyncio.gather(*[_check_one(mid, info) for mid, info in external_modules.items()])
    return {"updates": dict(results)}


@app.get("/modules/{module_id}")
async def get_module_detail(module_id: str):
    """Return full manifest + live env var status + command availability."""
    from src.utils.setup.module_registry import get_manifest, get_installed_modules
    try:
        manifest = get_manifest(module_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{module_id}' not found")

    installed = module_id in get_installed_modules()
    setup = manifest.get("setup", {})

    # Resolve env vars with live status (check module .env first, then os.environ)
    module_env = read_env_file(module_id)
    env_vars_raw = setup.get("env_vars", [])
    env_vars = []
    for var in env_vars_raw:
        # Support both plain strings ("VAR_NAME") and dicts ({"key": "VAR_NAME", ...})
        var_name = var["key"] if isinstance(var, dict) else var
        is_set = bool(module_env.get(var_name) or os.environ.get(var_name))
        entry = {"name": var_name, "set": is_set}
        if isinstance(var, dict):
            if "label" in var:
                entry["label"] = var["label"]
            if var.get("secret"):
                entry["secret"] = True
            if "placeholder" in var:
                entry["placeholder"] = var["placeholder"]
        env_vars.append(entry)

    # Resolve steps with live availability check
    steps_raw = setup.get("steps", [])
    steps = []
    for step in steps_raw:
        s = dict(step)
        if s.get("type") == "check_command":
            s["available"] = shutil.which(s.get("command", "")) is not None
        elif s.get("type") == "check_connectivity":
            cmd = s.get("command", "")
            try:
                result = subprocess.run(
                    shlex.split(cmd),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                s["available"] = result.returncode == 0
                raw = (result.stdout or result.stderr or "").strip()
                s["output"] = raw[:300] if raw else ""
            except subprocess.TimeoutExpired:
                s["available"] = False
                s["output"] = "Timed out after 10s"
            except FileNotFoundError:
                s["available"] = False
                s["output"] = f"Command not found: {cmd.split()[0] if cmd else '?'}"
            except Exception as e:
                s["available"] = False
                s["output"] = str(e)[:300]
        steps.append(s)

    has_configurations = bool(setup.get("configurations", {}).get("types"))

    # For external modules, read source URL and git SHA
    source_url = None
    git_sha = None
    try:
        from src.utils.setup.module_registry import get_module_package, INSTALLED_DIR
        pkg = get_module_package(module_id)
        if pkg == "installed":
            module_dir = INSTALLED_DIR / module_id
            url_file = module_dir / ".source_url"
            if url_file.exists():
                source_url = url_file.read_text().strip()
            sha_file = module_dir / ".git_sha"
            if sha_file.exists():
                git_sha = sha_file.read_text().strip()
            # Backfill: read installed SHA from local .git history (not remote)
            if not git_sha and (module_dir / ".git").exists():
                try:
                    r = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        capture_output=True, text=True, cwd=module_dir, timeout=5,
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        git_sha = r.stdout.strip()
                        (module_dir / ".git_sha").write_text(git_sha + "\n")
                except Exception:
                    pass
    except KeyError:
        pass

    return {
        "id": manifest["id"],
        "name": manifest.get("name", module_id),
        "version": manifest.get("version", ""),
        "description": manifest.get("description", ""),
        "nodes": manifest.get("nodes", []),
        "color": manifest.get("color", "#666666"),
        "installed": installed,
        "load_error": get_load_errors().get(module_id),
        "has_configurations": has_configurations,
        "source_url": source_url,
        "git_sha": git_sha,
        "setup": {
            "env_vars": env_vars,
            "steps": steps,
            **({"install_notes": setup["install_notes"]} if "install_notes" in setup else {}),
        },
    }


class InstallModuleRequest(BaseModel):
    env_vars: Dict[str, str] = {}


@app.post("/modules/{module_id}/install")
async def install_module_endpoint(module_id: str, body: InstallModuleRequest):
    """Call module's install.py if present, else default: write env vars + mark installed."""
    from src.utils.setup.module_registry import get_manifest, get_module_package
    try:
        manifest = get_manifest(module_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{module_id}' not found")

    # Try to load the module's own install script
    pkg = get_module_package(module_id)
    module_base = PROJECT_ROOT / pkg / module_id
    install_py = module_base / "install.py"
    if install_py.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location(f"{pkg}.{module_id}.install", install_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result = mod.install(body.env_vars)
    else:
        # Default: write provided env vars to modules/<id>/.env
        for key, value in body.env_vars.items():
            if value:
                write_env_var(key, value, module_id=module_id)
        steps = manifest.get("setup", {}).get("steps", [])
        manual_steps = [s for s in steps if s.get("type") == "run_command" and s.get("interactive")]
        result = {"success": True, "manual_steps": manual_steps}

    invalidate_node_registry()

    # Install module Python dependencies if requirements.txt is present
    req_file = module_base / "requirements.txt"
    if req_file.exists():
        logger.info("Installing Python dependencies for module '%s'…", module_id)
        pip_result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
            capture_output=True, text=True, timeout=300,
        )
        if pip_result.returncode != 0:
            logger.warning("pip install for module '%s' failed:\n%s", module_id, pip_result.stderr)
        else:
            logger.info("pip install for module '%s' succeeded.", module_id)

    result["needs_restart"] = True
    return result


class UpdateEnvRequest(BaseModel):
    env_vars: Dict[str, str] = {}


@app.post("/modules/{module_id}/env")
async def update_module_env(module_id: str, body: UpdateEnvRequest):
    """Update env vars for an installed module (writes to modules/<id>/.env)."""
    from src.utils.setup.module_registry import get_manifest, get_installed_modules
    try:
        get_manifest(module_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{module_id}' not found")
    if module_id not in get_installed_modules():
        raise HTTPException(status_code=400, detail=f"Module '{module_id}' is not installed")

    for key, value in body.env_vars.items():
        if value:
            write_env_var(key, value, module_id=module_id)
            os.environ[key] = value

    return {"success": True}


# ── Module Configurations (generic, manifest-driven) ───────────────────────

@app.get("/modules/{module_id}/configurations")
async def get_module_configurations(module_id: str):
    """Return configuration items with live status + type schema from manifest."""
    from src.utils.setup.config_registry import (
        get_configurations, get_configurations_masked, check_configuration_status,
        get_config_types, get_config_label,
    )
    from src.utils.setup.module_registry import get_manifest
    try:
        get_manifest(module_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{module_id}' not found")

    types = get_config_types(module_id)
    if not types:
        return {"items": [], "types": {}, "label": "Configurations"}

    items_decrypted = get_configurations(module_id)
    items_masked = get_configurations_masked(module_id)

    result = []
    for item, masked_item in zip(items_decrypted, items_masked):
        status = check_configuration_status(module_id, item)
        result.append({**masked_item, "status": status})

    return {
        "items": result,
        "types": types,
        "label": get_config_label(module_id),
    }


class ConfigurationsPayload(BaseModel):
    items: List[Dict[str, Any]]


@app.put("/modules/{module_id}/configurations")
async def save_module_configurations(module_id: str, body: ConfigurationsPayload):
    """Replace the full configuration list for a module (validates, resolves sentinels, saves)."""
    from src.utils.setup.config_registry import (
        get_configurations, set_configurations, get_config_types,
        _secret_keys_for_type, SECRET_SET_SENTINEL,
    )
    from src.utils.setup.module_registry import get_manifest
    try:
        get_manifest(module_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Module '{module_id}' not found")

    types = get_config_types(module_id)
    if not types:
        raise HTTPException(status_code=400, detail=f"Module '{module_id}' does not declare configurations")

    # Validation
    seen_names: set = set()
    for item in body.items:
        name = item.get("name", "").strip()
        itype = item.get("type", "")

        if not name:
            raise HTTPException(status_code=422, detail="Each configuration must have a non-empty 'name'")
        if name in seen_names:
            raise HTTPException(status_code=422, detail=f"Duplicate name: '{name}'")
        seen_names.add(name)

        if itype not in types:
            raise HTTPException(
                status_code=422,
                detail=f"'{name}': unknown type '{itype}'. Must be one of: {list(types)}",
            )

        type_def = types[itype]
        opts = item.get("options") or {}
        missing = [
            opt["label"]
            for opt in type_def.get("options", [])
            if opt.get("required") and not opts.get(opt["key"], "").strip()
        ]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"'{name}': missing required field(s): {', '.join(missing)}",
            )

    # Resolve sentinels: preserve existing secret values when __SET__ is sent
    existing_by_name = {item["name"]: item for item in get_configurations(module_id)}

    merged = []
    for item in body.items:
        name = item["name"].strip()
        itype = item.get("type", "")
        opts = dict(item.get("options") or {})

        secret_keys = _secret_keys_for_type(module_id, itype)
        ex_opts = (existing_by_name.get(name) or {}).get("options") or {}

        for key in secret_keys:
            submitted = opts.get(key, "")
            if submitted == SECRET_SET_SENTINEL:
                existing_val = ex_opts.get(key, "")
                if existing_val:
                    opts[key] = existing_val
                else:
                    opts.pop(key, None)
            elif not submitted:
                opts.pop(key, None)

        merged.append({**item, "name": name, "options": opts})

    set_configurations(module_id, merged)
    return {"ok": True}


class GithubInstallRequest(BaseModel):
    url: str


@app.post("/modules/install-from-github")
async def install_module_from_github(body: GithubInstallRequest):
    """Clone a module from a GitHub URL and install it."""
    raw_url = body.url.strip().rstrip("/").removesuffix(".git")

    try:
        clone_url, subpath, branch = _parse_github_url(raw_url)
    except HTTPException:
        raise HTTPException(status_code=400, detail="Invalid GitHub URL. Expected https://github.com/user/repo or .../tree/branch/path")

    tmpdir = tempfile.mkdtemp()
    try:
        clone_cmd = ["git", "clone", "--depth=1"]
        if branch:
            clone_cmd += ["-b", branch]
        clone_cmd += [clone_url, tmpdir]
        result = subprocess.run(
            clone_cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail=f"git clone failed: {(result.stderr or result.stdout).strip()[:300]}",
            )

        # Capture the commit SHA of the cloned HEAD
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=tmpdir, timeout=10,
        )
        git_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""

        module_src = Path(tmpdir) / subpath if subpath else Path(tmpdir)
        manifest_path = module_src / "manifest.json"

        if not manifest_path.exists():
            raise HTTPException(
                status_code=400,
                detail=f"No manifest.json found in {'/' + subpath if subpath else 'repo root'}.",
            )

        with open(manifest_path) as f:
            manifest = json.load(f)

        module_id = manifest.get("id")
        if not module_id:
            raise HTTPException(status_code=400, detail="manifest.json is missing required 'id' field.")

        from src.utils.setup.module_registry import get_installed_modules, INSTALLED_DIR
        if module_id in get_installed_modules():
            raise HTTPException(
                status_code=409,
                detail=f"A module with id '{module_id}' is already installed. Uninstall it first.",
            )

        INSTALLED_DIR.mkdir(exist_ok=True)
        dest = INSTALLED_DIR / module_id
        if dest.exists():
            def _force_remove_readonly(func, path, exc_info):
                os.chmod(path, stat.S_IWRITE)
                func(path)
            shutil.rmtree(dest, onerror=_force_remove_readonly)
        shutil.copytree(str(module_src), str(dest))

        # Save the source URL and commit SHA for display/update in the UI
        (dest / ".source_url").write_text(raw_url + "\n")
        if git_sha:
            (dest / ".git_sha").write_text(git_sha + "\n")

    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=400, detail="git clone timed out after 60s.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    invalidate_node_registry()

    # Install module Python dependencies if requirements.txt is present
    req_file = dest / "requirements.txt"
    if req_file.exists():
        logger.info("Installing Python dependencies for module '%s'…", module_id)
        pip_result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
            capture_output=True, text=True, timeout=300,
        )
        if pip_result.returncode != 0:
            logger.warning("pip install for module '%s' failed:\n%s", module_id, pip_result.stderr)
        else:
            logger.info("pip install for module '%s' succeeded.", module_id)

    # Best-effort post-install hooks — full reliability requires server restart
    for hook_name in ("on_startup", "register_routes"):
        try:
            from src.utils.setup.module_registry import get_module_package
            pkg = get_module_package(module_id)
            mod = importlib.import_module(f"{pkg}.{module_id}")
            hook = getattr(mod, hook_name, None)
            if callable(hook):
                hook(app) if hook_name == "register_routes" else hook()
        except Exception as e:
            logger.warning("Module '%s' %s failed after install: %s", module_id, hook_name, e)

    try:
        from src.utils.setup.langfuse_helper import get_langfuse_client, register_prompts
        register_prompts(get_langfuse_client())
    except Exception as e:
        logger.warning("Prompt sync failed after installing '%s': %s", module_id, e)

    return {
        "id": module_id,
        "name": manifest.get("name", module_id),
        "version": manifest.get("version", ""),
        "installed": True,
        "needs_restart": True,
    }


@app.post("/modules/{module_id}/uninstall")
async def uninstall_module_endpoint(module_id: str):
    """Uninstall a module by deleting its directory from installed/."""
    from src.utils.setup.module_registry import INSTALLED_DIR
    module_dir = INSTALLED_DIR / module_id
    if not module_dir.exists():
        raise HTTPException(status_code=400, detail=f"Module '{module_id}' is a built-in module and cannot be uninstalled.")
    def _force_remove_readonly(func, path, exc_info):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    shutil.rmtree(module_dir, onerror=_force_remove_readonly)
    invalidate_node_registry()

    # Sync prompts so stale prompts from the removed module get archived
    try:
        from src.utils.setup.langfuse_helper import get_langfuse_client, register_prompts
        register_prompts(get_langfuse_client())
    except Exception as e:
        logger.warning("Prompt sync failed after uninstalling '%s': %s", module_id, e)

    return {"success": True, "needs_restart": True}


def _parse_github_url(raw_url: str):
    """Parse a GitHub URL into (clone_url, subpath, branch).

    Returns (clone_url, subpath, branch) where branch may be None for default.
    Raises HTTPException on invalid URLs.
    """
    tree_match = re.match(
        r"(https://github\.com/[^/]+/[^/]+)/tree/([^/]+)(?:/(.+))?",
        raw_url,
    )
    if tree_match:
        return tree_match.group(1), tree_match.group(3) or "", tree_match.group(2)
    elif re.match(r"https://github\.com/[^/]+/[^/]+$", raw_url):
        return raw_url, "", None
    else:
        raise HTTPException(status_code=400, detail="Stored source URL is not a valid GitHub URL")


def _get_remote_sha(clone_url: str, branch: Optional[str] = None) -> str:
    """Use `git ls-remote` to get the HEAD SHA of a remote repo without cloning.

    This is significantly faster than a full clone — typically <1 second vs 10-30s.
    """
    ref = f"refs/heads/{branch}" if branch else "HEAD"
    result = subprocess.run(
        ["git", "ls-remote", clone_url, ref],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail=f"git ls-remote failed: {(result.stderr or result.stdout).strip()[:200]}",
        )
    # Output format: "<sha>\t<ref>\n"
    line = result.stdout.strip().split("\n")[0] if result.stdout.strip() else ""
    if not line:
        raise HTTPException(status_code=400, detail="Could not resolve remote HEAD — empty ls-remote output")
    return line.split("\t")[0]


@app.get("/modules/{module_id}/check-update")
async def check_module_update(module_id: str):
    """Lightweight update check using git ls-remote (no clone needed).

    Returns:
      {"update_available": false, "local_sha": "abc1234", "remote_sha": "abc1234"}
      {"update_available": true,  "local_sha": "abc1234", "remote_sha": "def5678"}
    """
    from src.utils.setup.module_registry import INSTALLED_DIR

    module_dir = INSTALLED_DIR / module_id
    if not module_dir.exists():
        raise HTTPException(status_code=404, detail=f"Module '{module_id}' not found")

    source_url_file = module_dir / ".source_url"
    if not source_url_file.exists():
        raise HTTPException(status_code=400, detail="Not an external module (no source URL)")

    raw_url = source_url_file.read_text().strip()
    sha_file = module_dir / ".git_sha"
    local_sha = sha_file.read_text().strip() if sha_file.exists() else None

    clone_url, _, branch = _parse_github_url(raw_url)

    loop = asyncio.get_event_loop()
    remote_sha = await loop.run_in_executor(None, _get_remote_sha, clone_url, branch)

    return {
        "update_available": bool(local_sha and remote_sha and local_sha != remote_sha),
        "local_sha": local_sha[:7] if local_sha else None,
        "remote_sha": remote_sha[:7] if remote_sha else None,
    }


@app.post("/modules/{module_id}/update")
async def update_module_from_github(module_id: str):
    """Re-clone an externally installed module and update its files.

    Preserves the module's .env file across the update. Returns:
      {"status": "up_to_date", "sha": "abc1234"}  — already at latest commit
      {"status": "updated", "from_sha": "abc1234", "to_sha": "def5678"}  — updated
    """
    from src.utils.setup.module_registry import INSTALLED_DIR, get_module_package

    module_dir = INSTALLED_DIR / module_id
    if not module_dir.exists():
        raise HTTPException(status_code=404, detail=f"Module '{module_id}' not found in installed/")

    source_url_file = module_dir / ".source_url"
    if not source_url_file.exists():
        raise HTTPException(status_code=400, detail=f"Module '{module_id}' has no source URL — not an external module")

    raw_url = source_url_file.read_text().strip()

    # Read current SHA
    sha_file = module_dir / ".git_sha"
    old_sha = sha_file.read_text().strip() if sha_file.exists() else None

    clone_url, subpath, _ = _parse_github_url(raw_url)

    tmpdir = tempfile.mkdtemp()
    new_sha = ""
    manifest = {}
    try:
        result = subprocess.run(
            ["git", "clone", "--depth=1", clone_url, tmpdir],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail=f"git clone failed: {(result.stderr or result.stdout).strip()[:300]}",
            )

        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=tmpdir, timeout=10,
        )
        new_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else ""

        # Already up to date?
        if new_sha and old_sha and new_sha == old_sha:
            return {"status": "up_to_date", "sha": new_sha[:7]}

        module_src = Path(tmpdir) / subpath if subpath else Path(tmpdir)
        manifest_path = module_src / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(status_code=400, detail="No manifest.json found in updated repo")

        with open(manifest_path) as f:
            manifest = json.load(f)

        # Preserve .env file across the update
        env_backup = None
        env_file = module_dir / ".env"
        if env_file.exists():
            env_backup = env_file.read_text()

        # Replace module directory (onerror handles read-only .git pack files on Windows)
        def _force_remove_readonly(func, path, exc_info):
            os.chmod(path, stat.S_IWRITE)
            func(path)
        shutil.rmtree(module_dir, onerror=_force_remove_readonly)
        shutil.copytree(str(module_src), str(module_dir))

        # Restore preserved files
        (module_dir / ".source_url").write_text(raw_url + "\n")
        if new_sha:
            (module_dir / ".git_sha").write_text(new_sha + "\n")
        if env_backup is not None:
            (module_dir / ".env").write_text(env_backup)

    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=400, detail="git clone timed out after 60s.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    invalidate_node_registry()

    # Evict stale Python modules so hooks run the new code
    stale_prefix = f"installed.{module_id}"
    for key in [k for k in sys.modules if k == stale_prefix or k.startswith(stale_prefix + ".")]:
        del sys.modules[key]

    # Best-effort post-update hooks — full reliability requires server restart
    try:
        pkg = get_module_package(module_id)
        updated_mod = importlib.import_module(f"{pkg}.{module_id}")
        if callable(getattr(updated_mod, "on_startup", None)):
            updated_mod.on_startup()
    except Exception as e:
        logger.warning("Module '%s' on_startup failed after update: %s", module_id, e)

    try:
        from src.utils.setup.langfuse_helper import get_langfuse_client, register_prompts
        register_prompts(get_langfuse_client())
    except Exception as e:
        logger.warning("Prompt sync failed after updating '%s': %s", module_id, e)

    return {
        "status": "updated",
        "from_sha": old_sha[:7] if old_sha else None,
        "to_sha": new_sha[:7] if new_sha else None,
        "name": manifest.get("name", module_id),
        "needs_restart": True,
    }


async def _module_has_warnings(module_id: str, manifest: dict, is_installed: bool) -> bool:
    """Check env vars, command availability, and connectivity (with 3s timeout per step)."""
    if not is_installed:
        return False
    setup = manifest.get("setup", {})
    module_env = read_env_file(module_id)
    for var in setup.get("env_vars", []):
        var_name = var if isinstance(var, str) else var.get("name", "")
        if var_name and not (module_env.get(var_name) or os.environ.get(var_name)):
            return True
    loop = asyncio.get_event_loop()
    for step in setup.get("steps", []):
        stype = step.get("type")
        if stype == "check_command":
            if not shutil.which(step.get("command", "")):
                return True
        elif stype == "check_connectivity":
            cmd = step.get("command", "")
            try:
                def _run():
                    return subprocess.run(
                        cmd.split(), capture_output=True, text=True, timeout=10
                    )
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, _run), timeout=10
                )
                if result.returncode != 0:
                    return True
            except Exception:
                return True

    # Check configurations (if declared)
    config_types = setup.get("configurations", {}).get("types")
    if config_types:
        from src.utils.setup.config_registry import get_configurations, check_configuration_status
        try:
            configs = get_configurations(module_id)
            if not configs:
                return True  # has types declared but none configured
            for cfg in configs:
                status = check_configuration_status(module_id, cfg)
                if not status.get("available"):
                    return True
        except Exception:
            pass

    return False


@app.get("/modules")
async def list_modules():
    """List all available modules with their install status and metadata."""
    from src.utils.setup.module_registry import get_all_manifests, get_installed_modules
    manifests = get_all_manifests()
    installed = set(get_installed_modules())
    load_errors = get_load_errors()

    async def _make_entry(module_id: str, manifest: dict) -> dict:
        from src.utils.setup.module_registry import get_module_package, _iter_module_dirs
        is_installed = module_id in installed
        try:
            pkg = get_module_package(module_id)
            origin = "external" if pkg == "installed" else "builtin"
        except KeyError:
            pkg = "modules"
            origin = "builtin"
        # Read source URL and git SHA for external modules
        source_url = None
        git_sha = None
        if origin == "external":
            for d, p in _iter_module_dirs():
                if d.name == module_id:
                    url_file = d / ".source_url"
                    if url_file.exists():
                        source_url = url_file.read_text().strip()
                    sha_file = d / ".git_sha"
                    if sha_file.exists():
                        git_sha = sha_file.read_text().strip()
                    # Backfill: read the installed SHA from the local .git history
                    # (never query the remote — that would overwrite the installed SHA
                    # with the current remote HEAD, breaking update detection)
                    if not git_sha and (d / ".git").exists():
                        try:
                            r = subprocess.run(
                                ["git", "rev-parse", "HEAD"],
                                capture_output=True, text=True, cwd=d, timeout=5,
                            )
                            if r.returncode == 0 and r.stdout.strip():
                                git_sha = r.stdout.strip()
                                (d / ".git_sha").write_text(git_sha + "\n")
                        except Exception:
                            pass
                    break
        return {
            "id": module_id,
            "name": manifest.get("name", module_id),
            "version": manifest.get("version", ""),
            "description": manifest.get("description", ""),
            "nodes": manifest.get("nodes", []),
            "color": manifest.get("color", "#666666"),
            "origin": origin,
            "source_url": source_url,
            "git_sha": git_sha,
            "installed": is_installed,
            "has_warnings": await _module_has_warnings(module_id, manifest, is_installed),
            "load_error": load_errors.get(module_id),
        }

    result = await asyncio.gather(*[_make_entry(mid, m) for mid, m in manifests.items()])
    return {"modules": list(result)}


# --- Global Variables ---

@app.get("/variables")
async def get_variables():
    """Return all global variables."""
    from src.utils.setup.variables_registry import get_all_variables
    return {"variables": get_all_variables()}


@app.put("/variables")
async def update_variables(body: dict):
    """Replace all global variables."""
    from src.utils.setup.variables_registry import set_all_variables
    variables = body.get("variables", {})
    set_all_variables(variables)
    return {"status": "ok", "count": len(variables)}


def _build_graph_tree(base: Path, current: Path) -> list:
    """Recursively build a folder/graph tree under `current`."""
    entries = []
    try:
        items = sorted(current.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except PermissionError:
        return entries
    for item in items:
        if item.name.startswith("."):
            continue
        rel = item.relative_to(base).as_posix()
        if item.is_dir():
            children = _build_graph_tree(base, item)
            if children:  # omit empty folders
                entries.append({"type": "folder", "name": item.name, "path": rel, "children": children})
        elif item.is_file() and item.suffix == ".json":
            entries.append({"type": "graph", "name": item.name, "path": rel})
    return entries


@app.get("/list_graphs")
async def list_graphs():
    """List JSON graph files as a folder tree plus a flat list for the visual editor."""
    if not GRAPH_SAVE_PATH.exists():
        return {"tree": [], "graphs": []}
    tree = _build_graph_tree(GRAPH_SAVE_PATH, GRAPH_SAVE_PATH)

    def flatten(nodes: list) -> list:
        result = []
        for n in nodes:
            if n["type"] == "graph":
                result.append(n["path"])
            else:
                result.extend(flatten(n["children"]))
        return result

    return {"tree": tree, "graphs": flatten(tree)}


@app.post("/stop/{thread_id:path}")
async def stop_execution(thread_id: str):
    from src.utils.setup.logger import thread_id_scope
    with thread_id_scope(thread_id):
        if thread_id in active_tasks:
            # Update status immediately for instant UI feedback
            active_tasks[thread_id]["status"] = "interrupted"
            active_tasks[thread_id]["active_nodes"] = set()
            active_tasks[thread_id]["stop_requested"] = True
            
            # Cancel the asyncio task for termination
            task = active_tasks[thread_id].get("task")
            if task and not task.done():
                task.cancel()
                logger.info("=" * 80)
                logger.info("STOP COMMAND RECEIVED FOR THREAD: %s", thread_id)
                logger.info("Task cancellation requested")
                logger.info("=" * 80)
                
                # We return immediately to avoid blocking the API while the task cleans up
                return {"message": "Execution stopped successfully", "status": "interrupted"}
            
            logger.info("=" * 80)
            logger.info("STOP COMMAND RECEIVED FOR THREAD: %s", thread_id)
            return {"message": "Stop request sent (task already completed or not found)"}
        return {"message": "Task not running or not found"}

@app.delete("/reset/{thread_id:path}")
async def reset_thread(thread_id: str):
    """Clear checkpoint memory for a specific thread."""
    from src.utils.setup.logger import thread_id_scope
    with thread_id_scope(thread_id):
        logger.info("=" * 80)
        logger.info("RESETTING THREAD MEMORY: %s", thread_id)
        try:
            if thread_id in active_tasks:
                # If a task is running, cancel it BEFORE deleting state to avoid collisions
                task = active_tasks[thread_id].get("task")
                if task and not task.done():
                    logger.info("Cancelling active task for thread %s during reset", thread_id)
                    task.cancel()
                del active_tasks[thread_id]

            async with create_checkpointer() as checkpointer:
                await checkpointer.adelete_thread(thread_id)
                
            # Clear caches
            # Clear logs: both in-memory and on-disk
            from src.utils.setup.logger import GLOBAL_LOG_BUFFER
            from src.utils.setup.const import SESSION_LOGS_DIR
            
            GLOBAL_LOG_BUFFER.pop(thread_id, None)
            log_file = SESSION_LOGS_DIR / f"{thread_id}.log"
            if log_file.exists():
                log_file.unlink()
                logger.info("Persistent logs deleted for thread %s", thread_id)

            return {"message": "Memory and logs cleared for thread %s" % thread_id}
        except Exception as e:
            logger.error("Failed to reset thread %s: %s", thread_id, e)
            # Fallback: clear in-memory task state and buffer anyway
            if thread_id in active_tasks: del active_tasks[thread_id]
            from src.utils.setup.logger import GLOBAL_LOG_BUFFER
            GLOBAL_LOG_BUFFER.pop(thread_id, None)
            return {"message": "Reset partially successful", "error": str(e)}

@app.post("/step_back/{thread_id:path}")
async def step_back(thread_id: str):
    """Step back to the previous checkpoint in the execution history."""
    from src.utils.setup.logger import thread_id_scope
    with thread_id_scope(thread_id):
        logger.info("=" * 80)
        logger.info("STEP BACK REQUEST FOR THREAD: %s", thread_id)
        
        try:
            # Infer graph_id from thread_id
            parts = thread_id.split("_")
            graph_id = "_".join(parts[:-1]) if len(parts) > 1 else thread_id
            
            file_path = GRAPH_SAVE_PATH / f"{graph_id}.json"
            if not file_path.exists():
                return {"status": "error", "message": f"Graph definition for {graph_id} not found"}
            
            with open(file_path, "r") as f:
                graph_json = json.load(f)

            # Build predecessor map from graph topology for as_node resolution
            # on source=update targets (step-back artifacts).
            # Uses extract_edges (which resolves portals) instead of raw links,
            # so portal nodes don't pollute the predecessor map.
            from src.graphs.graph_factory import get_node_id, flatten_graph_json, extract_edges
            flat_json = flatten_graph_json(graph_json)
            edges = extract_edges(flat_json)
            predecessor_map = {}  # unique_id -> list of predecessor unique_ids
            for edge in edges:
                src_uid = edge["source"]
                tgt_uid = edge["target"]
                predecessor_map.setdefault(tgt_uid, [])
                if src_uid not in predecessor_map[tgt_uid]:
                    predecessor_map[tgt_uid].append(src_uid)

            async with create_checkpointer() as checkpointer:
                workflow = build_langgraph_from_json(graph_json, get_node_registry(), graph_id=graph_id)
                interrupts = extract_interrupts(graph_json)
                graph_runnable = workflow.compile(checkpointer=checkpointer, interrupt_before=interrupts)
                
                config = {"configurable": {"thread_id": thread_id}}
                
                # Get current state
                current_state = await graph_runnable.aget_state(config)
                
                if not current_state.values:
                    logger.warning("No checkpoint history found for thread %s", thread_id)
                    return {"status": "error", "message": "No checkpoint history available"}
                
                # Get state history
                state_history = []
                async for s in graph_runnable.aget_state_history(config):
                    state_history.append(s)
                
                if not state_history:
                    logger.warning("No checkpoint history found for thread %s", thread_id)
                    return {"status": "error", "message": "No checkpoint history available"}

                # LOG HISTORY for debugging parallel supersteps
                logger.info("--- State History Discovery ---")
                for i, s in enumerate(state_history[:15]):
                    m = s.metadata or {}
                    logger.info("  [%d] step=%s, source=%s, next=%s, writes=%s", 
                                i, m.get("step"), m.get("source"), s.next, m.get("writes"))

                # 2. FIND PREVIOUS SUPERSTEP using graph topology.
                # Use the predecessor map to determine which node(s) PRECEDE the
                # current "next" node(s). Then search history for a checkpoint
                # whose 'next' matches those predecessors. This is immune to stale
                # step-back artifacts polluting the history.
                current_meta = current_state.metadata or {}
                current_step = current_meta.get("step", 0)

                previous_state = None
                current_next = list(current_state.next) if current_state.next else []

                # If current_next is empty (e.g. after a graph error or completion),
                # fall back to the first history entry with a non-empty next.
                if not current_next and state_history:
                    for s in state_history:
                        if s.next:
                            current_next = list(s.next)
                            s_meta = s.metadata or {}
                            current_step = s_meta.get("step", current_step)
                            logger.info("Step back: current_state.next was empty, using history fallback: next=%s, step=%s", current_next, current_step)
                            break

                # Find predecessors for each current next node
                target_nexts = set()
                for n in current_next:
                    preds = predecessor_map.get(n, [])
                    target_nexts.update(preds)

                if not target_nexts:
                    return {"status": "error", "message": "Already at the beginning — nothing to step back to"}

                logger.info("Step back: current next=%s, looking for predecessor states: %s", current_next, target_nexts)

                # Search history for a checkpoint whose next matches a predecessor.
                # Only consider entries with a LOWER step number than current —
                # entries at the same or higher step represent the same or forward
                # execution positions (including step-back artifacts and cyclic
                # graph iterations).
                for s in state_history[1:]:
                    s_meta = s.metadata or {}
                    s_step = s_meta.get("step", 0)
                    if s_step >= current_step:
                        continue
                    s_next = set(s.next) if s.next else set()
                    if s_next & target_nexts:
                        previous_state = s
                        break

                if not previous_state:
                    return {"status": "error", "message": "No previous checkpoint found for predecessor nodes"}

                # Identify the checkpoint we want to revert to (previous state)
                previous_checkpoint_id = previous_state.config.get("configurable", {}).get("checkpoint_id")
                previous_ns = previous_state.config.get("configurable", {}).get("checkpoint_ns", "")
                
                logger.info(">> REVERTING TO: step=%s, checkpoint=%s (ns='%s')", 
                            (previous_state.metadata or {}).get("step"), previous_checkpoint_id, previous_ns)
                
                # 3. REPLACEMENT MODE: To truly "step back", we must ensure the new checkpoint 
                # has EXACTLY the values of the previous one. We do this by adding tombstones 
                # for any keys that exist in current state but not in the previous state.
                local_values = {**previous_state.values}
                
                # Collect ALL keys from the current checkpoint to find what to tombstone
                current_keys = set()
                if current_state.values:
                    for k in current_state.values:
                        if k != "__root__" and not (isinstance(k, str) and k.startswith("__")):
                            current_keys.add(k)
                    root_data = current_state.values.get("__root__")
                    if isinstance(root_data, dict):
                        for k in root_data:
                            if not (isinstance(k, str) and k.startswith("__")):
                                current_keys.add(k)
                
                # Never tombstone thread_id or @@namespace keys (they are structural)
                preserved_keys = {"thread_id"} | {k for k in current_keys if isinstance(k, str) and k.endswith("@@namespace")}
                tombstones = {k: None for k in current_keys if k not in local_values and k not in preserved_keys}
                
                if tombstones:
                    logger.info("Step back: Adding tombstones for %d orphaned keys", len(tombstones))
                    local_values.update(tombstones)

                # Ensure thread_id persists
                if "thread_id" not in local_values:
                    local_values["thread_id"] = thread_id

                # 4. RESOLVE AS_NODE to avoid "Ambiguous update" errors
                # If the checkpoint we are forking from has multiple pending tasks, 
                # we MUST specify which node's "leg" we are updating.
                as_node = None
                meta = previous_state.metadata or {}
                source = meta.get("source")
                previous_step = meta.get("step", 0)
                
                logger.info("Step back metadata: %s", meta)
                
                if source == "loop":
                    # If it came from a node execution, the 'writes' dict tells us which node(s)
                    writes = meta.get("writes")
                    if writes and isinstance(writes, dict):
                        # Use the first node that wrote to this checkpoint
                        as_node = list(writes.keys())[0]
                    elif not writes and previous_state.next:
                        # writes=None means this is a "superstep start" checkpoint.
                        # Find the parent checkpoint to determine what nodes produced this state.
                        previous_checkpoint_id_local = previous_state.config.get("configurable", {}).get("checkpoint_id")
                        parent_state = None
                        found_previous = False
                        for s in state_history:
                            if not found_previous:
                                s_cp_id = s.config.get("configurable", {}).get("checkpoint_id")
                                if s_cp_id == previous_checkpoint_id_local:
                                    found_previous = True
                                continue
                            # First entry after previous_state is the parent
                            parent_state = s
                            break
                        
                        if parent_state and parent_state.next:
                            if len(parent_state.next) == 1:
                                # Single parent node produced this state — use it as as_node
                                as_node = list(parent_state.next)[0]
                                logger.info("Step back: single parent node as_node='%s'", as_node)
                            # else: parent has multiple next nodes — handled below by parallel logic
                elif source == "update":
                    # First check metadata (some updates store as_node)
                    as_node = meta.get("as_node")
                    # For update targets, resolve as_node from graph topology:
                    # find the predecessor of the next node (the node whose completion
                    # would schedule the target's next node).
                    if not as_node and previous_state.next:
                        next_node = list(previous_state.next)[0]
                        preds = predecessor_map.get(next_node, [])
                        if len(preds) == 1:
                            as_node = preds[0]
                            logger.info("Step back: update target, as_node='%s' (predecessor of '%s')", as_node, next_node)
                        elif len(preds) > 1:
                            # Multiple predecessors — pick the most recent one from state history
                            pred_set = set(preds)
                            for s in state_history:
                                if s.next:
                                    for n in s.next:
                                        if n in pred_set:
                                            as_node = n
                                            break
                                if as_node:
                                    break
                            if as_node:
                                logger.info("Step back: update target, as_node='%s' from history (predecessor of '%s')", as_node, next_node)
                
                # Fallback: if it's the very first step, use __start__
                if not as_node and previous_step == 0:
                    as_node = "__start__"
                
                # PARALLEL FAN-OUT HANDLING:
                # When writes=None and there are multiple next nodes, this is a "superstep start"
                # checkpoint. A single as_node won't restore all branches — we need to use
                # abulk_update_state with multiple StateUpdate entries in a single superstep.
                #
                # The nodes that completed to produce this superstep are the `next` nodes from
                # the checkpoint one step further back (the prior superstep start).
                if not as_node and previous_state.next and len(previous_state.next) > 1:
                    # Find the parent superstep start — the checkpoint whose branches produced
                    # the previous_state we want to restore.
                    previous_checkpoint_id_local = previous_state.config.get("configurable", {}).get("checkpoint_id")
                    parent_state = None
                    found_previous = False
                    for s in state_history:
                        if not found_previous:
                            s_cp_id = s.config.get("configurable", {}).get("checkpoint_id")
                            if s_cp_id == previous_checkpoint_id_local:
                                found_previous = True
                            continue
                        # First entry after previous_state is the parent
                        parent_state = s
                        break
                    
                    if parent_state and parent_state.next:
                        branch_nodes = list(parent_state.next)
                        logger.info("Step back: PARALLEL FAN-OUT detected with %d next nodes.", len(previous_state.next))
                        logger.info("Step back: parent step=%s has branch nodes: %s", 
                                    (parent_state.metadata or {}).get("step"), branch_nodes)
                        
                        # Use abulk_update_state with a single superstep containing one 
                        # StateUpdate per branch node. This tells LangGraph that all branches
                        # completed simultaneously, correctly restoring the full parallel fan-out.
                        from langgraph.types import StateUpdate
                        
                        superstep_updates = [
                            StateUpdate(values=local_values, as_node=node_name) 
                            for node_name in branch_nodes
                        ]
                        logger.info("Step back: calling abulk_update_state with %d updates: %s", 
                                    len(superstep_updates), [u.as_node for u in superstep_updates])
                        
                        parent_config = {**parent_state.config}
                        await graph_runnable.abulk_update_state(
                            config=parent_config,
                            supersteps=[superstep_updates]
                        )
                        
                        logger.info("Step back: all %d parallel branches restored via bulk update", len(branch_nodes))
                        as_node = "__PARALLEL_DONE__"

                logger.info("Step back: effective as_node='%s'", as_node)

                # Use LangGraph's update_state to revert to the previous checkpoint.
                # We pass the previous state's config which includes the checkpoint_id we are forking FROM.
                if as_node != "__PARALLEL_DONE__":
                    if not as_node:
                        error_msg = (f"Cannot resolve as_node for step back. "
                                     f"step={previous_step}, source={source}, "
                                     f"next={previous_state.next}, writes={meta.get('writes')}")
                        logger.error(error_msg)
                        return {"status": "error", "message": error_msg}
                    
                    update_config = {**previous_state.config}
                    
                    await graph_runnable.aupdate_state(
                        config=update_config, 
                        values=local_values,
                        as_node=as_node
                    )
                
                logger.info("Successfully stepped back to previous checkpoint for thread %s", thread_id)
                logger.info("=" * 80)
                
                def recursive_unwrap(obj):
                    if not isinstance(obj, dict): return obj
                    work_data = {**obj}
                    if "__root__" in work_data:
                        root_data = work_data.pop("__root__")
                        if isinstance(root_data, dict): work_data.update(root_data)
                    return {k: (recursive_unwrap(v) if isinstance(v, dict) else v) for k, v in work_data.items() if v is not None and not (isinstance(k, str) and (k.startswith("__") or k.endswith("@@namespace")))}

                state_values = recursive_unwrap(previous_state.values)

                return {
                    "status": "success",
                    "message": "Stepped back to previous checkpoint",
                    "previous_checkpoint": previous_state.config.get("configurable", {}).get("checkpoint_id"),
                    "state": json.loads(json.dumps(state_values, default=str))
                }
                
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            logger.error("Failed to step back for thread %s:\n%s", thread_id, error_trace)
            return {"status": "error", "message": str(e), "traceback": error_trace}

@app.post("/seed_state")
async def seed_state(request: SeedStateRequest):
    """Mark a node as completed and update the state with user-edited values.
    
    Uses as_node directly — telling LangGraph that this node has finished
    and its output is the provided values. The next nodes will be the
    downstream neighbors of the marked node.
    """
    thread_id = request.thread_id
    root_graph_id = request.root_graph_id
    if root_graph_id.endswith(".json"):
        root_graph_id = root_graph_id[:-5]

    checkpoint_ns = request.checkpoint_ns
    values = request.values
    as_node = request.as_node  # The node being marked as completed
    
    try:
        # Load ROOT graph from disk (Source of Truth)
        file_path = GRAPH_SAVE_PATH / f"{root_graph_id}.json"
        if not file_path.exists():
            return {"status": "error", "message": f"Graph definition '{root_graph_id}' not found on disk."}

        with open(file_path, "r", encoding="utf-8") as f:
            root_json = json.load(f)
        logger.info("Loaded graph '%s' from disk for seed_state", root_graph_id)

        checkpointer = getattr(app.state, 'checkpointer', None)
        
        async def run_seed_state(cp):
            workflow = build_langgraph_from_json(root_json, get_node_registry(), graph_id=root_graph_id)
            interrupts = extract_interrupts(root_json)
            graph_runnable = workflow.compile(checkpointer=cp, interrupt_before=interrupts)

            # 1. Namespace Discovery
            resolved_ns, residual_prefix, resolved_all = await resolve_checkpoint_ns(graph_runnable, thread_id, checkpoint_ns)
            if checkpoint_ns and not resolved_all:
                 return {"status": "error", "message": f"Could not resolve subgraph path: {checkpoint_ns}. Ensure the subgraph has started."}

            # For inline subgraphs, the residual_prefix gives us the inlined path prefix.
            # LangGraph needs the full path (e.g. "SUBGRAPH@test_graph_7@@DelayNode_3")
            # to correctly scope the update to the namespaced node.
            effective_as_node = f"{residual_prefix}@@{as_node}" if residual_prefix else as_node
            if residual_prefix:
                logger.info("INLINE SUBGRAPH: prepended residual_prefix, effective_as_node='%s'", effective_as_node)

            # 3. TASK DISCOVERY: Find the exact task ID matching our as_node
            # This is critical for parallel branches to avoid "Ambiguous update" errors
            update_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": resolved_ns}}
            state_snapshot = await graph_runnable.aget_state(update_config)
            
            target_task_id = None
            if state_snapshot.tasks:
                for task in state_snapshot.tasks:
                    if task.name == effective_as_node:
                        target_task_id = task.id
                        logger.info("Found active task '%s' with ID: %s", effective_as_node, target_task_id)
                        break
            
            if target_task_id:
                update_config["configurable"]["task_id"] = target_task_id

            # 4. Replacement Mode: Tombstone keys removed by user
            local_values = values # Avoid re-assignment issues
            try:
                if not resolved_ns and not state_snapshot.values:
                     state_snapshot = await graph_runnable.aget_state({"configurable": {"thread_id": thread_id}})

                if state_snapshot.values and isinstance(state_snapshot.values, dict):
                    # Collect ALL keys from the checkpoint (both top-level and __root__)
                    all_keys = set()
                    for k in state_snapshot.values:
                        if k != "__root__" and not (isinstance(k, str) and k.startswith("__")):
                            all_keys.add(k)
                    root_data = state_snapshot.values.get("__root__")
                    if isinstance(root_data, dict):
                        for k in root_data:
                            if not (isinstance(k, str) and k.startswith("__")):
                                all_keys.add(k)

                    logger.info("Current keys in '%s': %s", resolved_ns or 'root', sorted(all_keys))
                    # Never tombstone thread_id or @@namespace keys
                    preserved_keys = {"thread_id"} | {k for k in all_keys if isinstance(k, str) and k.endswith("@@namespace")}
                    tombstones = {k: None for k in all_keys if k not in local_values and k not in preserved_keys}
                    if tombstones:
                        logger.info("Replacement mode: Cleared keys %s", list(tombstones.keys()))
                        local_values = {**tombstones, **local_values}
            except Exception as e:
                logger.warning("Could not fetch state for replacement logic: %s", e)

            # 4. Ensure thread_id is always injected — it's a framework key that must persist
            if "thread_id" not in local_values:
                local_values["thread_id"] = thread_id

            logger.info("Updating state (ns='%s', as_node='%s') with: %s", resolved_ns or 'root', effective_as_node, local_values)

            # 5. Update state — as_node tells LangGraph this node completed
            # If we found a specific task_id, it ensures we update the correct parallel branch
            await graph_runnable.aupdate_state(update_config, local_values, as_node=effective_as_node)

            # 6. Clear active_tasks so get_graph_status re-derives status from checkpoint
            if thread_id in active_tasks:
                del active_tasks[thread_id]

            return {"status": "success", "message": f"Marked '{effective_as_node}' as completed. Execution will resume from its downstream nodes."}

        if not checkpointer:
            async with create_checkpointer() as cp:
                return await run_seed_state(cp)
        else:
            return await run_seed_state(checkpointer)

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        logger.error("Failed to mark completed for thread %s:\n%s", thread_id, error_trace)
        return {"status": "error", "message": str(e), "traceback": error_trace}


# ── Module UI Serving ─────────────────────────────────────────────────────────
# Modules can provide custom UI pages by placing files in a `ui/` directory.
# Files are served at: /modules/{module_id}/ui/{path}
# This allows pluggable modules to provide custom UIs (review pages, dashboards, etc.)

@app.get("/modules/{module_id}/ui/{path:path}")
async def serve_module_ui(module_id: str, path: str):
    """Serve static UI files from a module's ui/ directory."""
    from fastapi.responses import FileResponse
    import mimetypes

    # Default to index.html for directory-like requests
    if not path or path.endswith("/"):
        path = path + "index.html"

    # Check installed/ first, then modules/
    for base_dir in [PROJECT_ROOT / "installed", PROJECT_ROOT / "modules"]:
        ui_dir = base_dir / module_id / "ui"
        file_path = ui_dir / path
        # Security: ensure resolved path is within the ui/ directory
        try:
            file_path = file_path.resolve()
            ui_dir_resolved = ui_dir.resolve()
            if not str(file_path).startswith(str(ui_dir_resolved)):
                continue
        except (OSError, ValueError):
            continue
        if file_path.is_file():
            content_type, _ = mimetypes.guess_type(str(file_path))
            return FileResponse(file_path, media_type=content_type)

    raise HTTPException(status_code=404, detail=f"UI file not found: {module_id}/ui/{path}")


@app.post("/restart")
async def restart_server():
    """Trigger a server-side restart of the process."""
    import sys
    import os
    import threading
    import time
    
    logger.info("=" * 80)
    logger.info("RESTART REQUEST RECEIVED FROM UI")
    logger.info("=" * 80)
    
    def perform_restart():
        time.sleep(1.0) # Give more time for the response to reach the UI
        # Clear terminal before restart
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=perform_restart, daemon=True).start()
    return {"message": "Restarting server process..."}

if __name__ == "__main__":
    import uvicorn

    if sys.platform == "win32":
        # psycopg async requires SelectorEventLoop, not the default ProactorEventLoop.
        # Bypass uvicorn.run() to ensure the correct event loop is used.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        uvicorn_config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_config=None)
        server = uvicorn.Server(uvicorn_config)
        asyncio.run(server.serve())
    else:
        uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)
