from typing import Any, Dict, List, Optional
import json
import re
from pathlib import Path

from src.nodes.abstract.router_node import RouterNode
from src.inputs.standard_inputs import Resolvable
from src.utils.ns_resolver import resolve_checkpoint_ns
from src.utils.setup.db import create_checkpointer
from src.utils.setup.const import GRAPH_SAVE_PATH
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)

class SubgraphNodeCompletionRouter(RouterNode):
    """Router node that checks if a specific node in a subgraph has completed execution.
    
    This node queries the LangGraph checkpointer to determine if the target node
    has finished its task. It distinguishes between 'waiting at interrupt' and 'finished'.
    """

    # Fixed route names
    COMPLETED = "completed"
    NOT_COMPLETED = "not_completed"

    def __init__(
        self,
        subgraph_node_id: int = 0,
        target_node_id: int = 0,
        thread_id: Resolvable[str] = "{{thread_id}}",
        **kwargs
    ):
        """Initialize the router.

        Args:
            subgraph_node_id: LiteGraph ID of the subgraph node in the parent graph
            target_node_id: LiteGraph ID of the node to check inside that subgraph
            thread_id: Thread ID to check completion against (template supported).
            **kwargs: Additional properties
        """
        super().__init__(**kwargs)
        self.subgraph_node_id = subgraph_node_id
        self.target_node_id = target_node_id
        self.thread_id = thread_id

    def get_route_options(self) -> List[str]:
        return [self.COMPLETED, self.NOT_COMPLETED]

    def _resolve_logical_id(self, node_id: int, graph_json: Dict[str, Any]) -> str:
        """Helper to convert a LiteGraph integer ID to a LangGraph logical string ID."""
        if not node_id or node_id <= 0:
            return ""
            
        # Search for the node by integer ID in the provided graph JSON
        from src.graphs.graph_factory import get_node_id
        
        for node in graph_json.get("nodes", []):
            if node.get("id") == node_id:
                return get_node_id(node)
                
        return str(node_id) # Fallback if not found

    async def get_route(self, state: Dict[str, Any]) -> str:
        thread_id = self._thread_id
        if not thread_id:
            return self.NOT_COMPLETED

        if not self.target_node_id or self.target_node_id <= 0:
            logger.warning("SubgraphNodeCompletionRouter: Invalid target_node_id (%s), defaulting to not_completed", self.target_node_id)
            return self.NOT_COMPLETED

        # 1. Determine root graph ID from thread_id
        parts = thread_id.split("_")
        root_graph_id = "_".join(parts[:-1]) if len(parts) > 1 else thread_id
        if root_graph_id.endswith(".json"):
            root_graph_id = root_graph_id[:-5]

        # 2. Load root graph JSON
        file_path = GRAPH_SAVE_PATH / f"{root_graph_id}.json"
        if not file_path.exists():
            logger.error("SubgraphNodeCompletionRouter: Root graph file not found: %s", file_path)
            return self.NOT_COMPLETED

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                root_json = json.load(f)
        except Exception as e:
            logger.error("SubgraphNodeCompletionRouter: Failed to load root graph JSON: %s", e)
            return self.NOT_COMPLETED

        # 3. Resolve IDs to logical strings
        # Subgraph node is in the root graph
        logical_sub_id = self._resolve_logical_id(self.subgraph_node_id, root_json)
        
        # For the target node, we need to load the subgraph JSON and resolve the ID there
        logical_target_id = str(self.target_node_id)
        
        # Find the subgraph node in the parent to get the correct subgraph JSON filename
        sub_node_data = None
        for n in root_json.get("nodes", []):
            if n.get("id") == self.subgraph_node_id:
                sub_node_data = n
                break
        
        if sub_node_data and sub_node_data.get("type") == "langgraph/subgraph":
            subgraph_name = sub_node_data.get("properties", {}).get("subgraph")
            if subgraph_name:
                sub_file = GRAPH_SAVE_PATH / subgraph_name
                if not sub_file.exists() and not subgraph_name.endswith(".json"):
                    sub_file = GRAPH_SAVE_PATH / f"{subgraph_name}.json"
                
                if sub_file.exists():
                    with open(sub_file, "r", encoding="utf-8") as f:
                        sub_json = json.load(f)
                    logical_target_id = self._resolve_logical_id(self.target_node_id, sub_json)

        # 4. Connect to checkpointer and check status
        try:
            from src.graphs.graph_factory import build_langgraph_from_json, extract_interrupts
            from src.utils.setup.node_registry import get_node_registry

            async with create_checkpointer() as cp:
                # Build runnable to use its state management methods
                registry = get_node_registry()
                workflow = build_langgraph_from_json(root_json, registry, graph_id=root_graph_id)
                interrupts = extract_interrupts(root_json)
                graph_runnable = workflow.compile(checkpointer=cp, interrupt_before=interrupts)

                # Resolve the namespace using the logical subgraph ID
                target_ns, residual_prefix, resolved_all = await resolve_checkpoint_ns(graph_runnable, thread_id, logical_sub_id)
                
                if logical_sub_id and not resolved_all:
                    logger.info("SubgraphNodeCompletionRouter: Subgraph node '%s' not yet resolved", logical_sub_id)
                    return self.NOT_COMPLETED

                # For inlined subgraphs, the residual_prefix contains the flattened
                # path (e.g. "SUBGRAPH@test_graph_json_7"). We must prepend it to the
                # target node name so we search for the fully-qualified ID
                # (e.g. "SUBGRAPH@test_graph_json_7@@DelayNode_3") instead of bare "DelayNode_3".
                qualified_target = f"{residual_prefix}@@{logical_target_id}" if residual_prefix else logical_target_id
                logger.info("SubgraphNodeCompletionRouter: qualified_target='%s' (residual_prefix='%s', logical_target='%s')",
                            qualified_target, residual_prefix, logical_target_id)

                # Fetch current state for that namespace
                config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": target_ns}}
                state_snapshot = await graph_runnable.aget_state(config)

                next_nodes = list(state_snapshot.next)
                logger.info("SubgraphNodeCompletionRouter: Checking completion for '%s'. Current next nodes: %s", 
                            qualified_target, next_nodes)

                # Check if it's currently pending/interrupted
                if qualified_target in state_snapshot.next:
                    logger.info("SubgraphNodeCompletionRouter: Node '%s' is waiting/interrupted", qualified_target)
                    return self.NOT_COMPLETED

                # Check history to see if it ever ran
                async for history_snapshot in graph_runnable.aget_state_history(config, limit=50):
                    metadata = history_snapshot.metadata or {}
                    if metadata.get("source") == "loop" and metadata.get("step") is not None:
                        for task in history_snapshot.tasks:
                            if task.name == qualified_target:
                                logger.info("SubgraphNodeCompletionRouter: Node '%s' found in history tasks, marked as completed", qualified_target)
                                return self.COMPLETED

                logger.info("SubgraphNodeCompletionRouter: Node '%s' not found in history or current status", logical_target_id)
                return self.NOT_COMPLETED

        except Exception as e:
            import traceback
            logger.error("SubgraphNodeCompletionRouter: Error checking completion: %s\n%s", e, traceback.format_exc())
            return self.NOT_COMPLETED
