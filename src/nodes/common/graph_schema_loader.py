"""Graph Schema Loader — Loads a graph's initial_state into the current state for LLM prompts."""

import json
from typing import Any, Dict

from src.nodes.abstract.base_node import BaseNode
from src.utils.setup.const import GRAPH_SAVE_PATH
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class GraphSchemaLoader(BaseNode):
    """Reads a target graph's JSON definition and puts its initial_state
    schema into state for downstream LLM nodes.
    
    Inputs:
      - graph_name: Name of the graph file to load.
      
    Outputs:
      - example_input: JSON string of the graph's initial_state.
    """

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        graph_name = state.get("graph_name")
        if not graph_name:
            raise ValueError("GraphSchemaLoader: No graph_name in state")

        graph_path = GRAPH_SAVE_PATH / f"{graph_name}.json"
        if not graph_path.exists():
            raise ValueError(f"GraphSchemaLoader: Graph file not found: {graph_path}")

        with open(graph_path, "r", encoding="utf-8") as f:
            graph_json = json.load(f)
            initial_state_str = graph_json.get("extra", {}).get("initial_state", "{}")

        return {
            "example_input": initial_state_str,
        }
