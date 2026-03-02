"""SubgraphStateValueGetter — retrieves a value from another subgraph's inner state."""

import json
from typing import Any, Dict

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.ns_resolver import resolve_checkpoint_ns
from src.utils.template_manager import render_template
from src.utils.setup.db import create_checkpointer
from src.utils.setup.const import GRAPH_SAVE_PATH
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class SubgraphStateValueGetter(BaseNode):
    """Retrieves a specific value from a subgraph's inner state.

    Queries the LangGraph checkpointer to read the subgraph's state, then
    resolves ``value_template`` against that state and writes the result to
    ``output_key`` in the parent state.

    Example:
        subgraph_node_id = 7          # LiteGraph ID of the subgraph node
        value_template   = "{{result}}"  # key inside the subgraph state
        output_key       = "my_result"   # key written to parent state
    """

    def __init__(
        self,
        subgraph_node_id: int = 0,
        thread_id: Resolvable[str] = "{{thread_id}}",
        value_template: str = "",
        output_key: Resolvable[str] = "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.subgraph_node_id = subgraph_node_id
        self.thread_id = thread_id
        self.value_template = value_template
        self.output_key = output_key

    def _resolve_logical_id(self, node_id: int, graph_json: Dict[str, Any]) -> str:
        """Convert a LiteGraph integer ID to a LangGraph logical string ID."""
        if not node_id or node_id <= 0:
            return ""

        from src.graphs.graph_factory import get_node_id

        for node in graph_json.get("nodes", []):
            if node.get("id") == node_id:
                return get_node_id(node)

        return str(node_id)

    async def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        thread_id = self._thread_id
        output_key = self._output_key

        if not thread_id:
            raise ValueError("SubgraphStateValueGetter: thread_id is required")
        if not output_key:
            raise ValueError("SubgraphStateValueGetter: output_key is required")
        if not self.value_template:
            raise ValueError("SubgraphStateValueGetter: value_template is required")

        # 1. Determine root graph ID from thread_id
        parts = thread_id.split("_")
        root_graph_id = "_".join(parts[:-1]) if len(parts) > 1 else thread_id
        if root_graph_id.endswith(".json"):
            root_graph_id = root_graph_id[:-5]

        # 2. Load root graph JSON
        file_path = GRAPH_SAVE_PATH / f"{root_graph_id}.json"
        if not file_path.exists():
            raise ValueError(f"SubgraphStateValueGetter: Root graph file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            root_json = json.load(f)

        # 3. Resolve subgraph node ID to logical LangGraph ID
        logical_sub_id = self._resolve_logical_id(self.subgraph_node_id, root_json)
        if not logical_sub_id:
            raise ValueError(
                f"SubgraphStateValueGetter: Could not resolve subgraph_node_id={self.subgraph_node_id}"
            )

        logger.info(
            "SubgraphStateValueGetter: Fetching state from subgraph '%s' (node %d)",
            logical_sub_id, self.subgraph_node_id,
        )

        # 4. Build graph runnable and resolve namespace
        from src.graphs.graph_factory import build_langgraph_from_json, extract_interrupts
        from src.utils.setup.node_registry import get_node_registry

        async with create_checkpointer() as cp:
            registry = get_node_registry()
            workflow = build_langgraph_from_json(root_json, registry, graph_id=root_graph_id)
            interrupts = extract_interrupts(root_json)
            graph_runnable = workflow.compile(checkpointer=cp, interrupt_before=interrupts)

            target_ns, residual_prefix, resolved_all = await resolve_checkpoint_ns(
                graph_runnable, thread_id, logical_sub_id,
            )

            if not resolved_all:
                raise ValueError(
                    f"SubgraphStateValueGetter: Subgraph '{logical_sub_id}' namespace not yet resolved"
                )

            # 5. Get the subgraph's state
            config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": target_ns}}
            state_snapshot = await graph_runnable.aget_state(config)
            subgraph_state = state_snapshot.values or {}

            logger.info(
                "SubgraphStateValueGetter: Got subgraph state with %d keys: %s",
                len(subgraph_state), list(subgraph_state.keys()),
            )

            # 6. Resolve value_template against the subgraph state
            resolved_value = render_template(self.value_template, subgraph_state)

            logger.info(
                "SubgraphStateValueGetter: Resolved '%s' → %s (writing to '%s')",
                self.value_template, type(resolved_value).__name__, output_key,
            )

            return {output_key: resolved_value}
