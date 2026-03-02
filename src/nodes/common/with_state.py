from typing import Any, Dict, Optional
import json
from src.nodes.abstract.base_node import BaseNode
from src.utils.setup.logger import get_logger

from src.inputs.standard_inputs import JSONString

logger = get_logger(__name__)

class WithState(BaseNode):
    """Node that injects static values or structured JSON data into the execution state.
    
    Useful for initializing a graph with specific configuration like project_key or repo_name
    without requiring them to be passed in the initial execution request.
    """
    
    def __init__(
        self,
        state_json: JSONString = "{}",
        **kwargs
    ):
        """Initialize the WithState node.

        Args:
            state_json: A JSON string containing additional key-value pairs to merge into state.
            **kwargs: Additional properties from LiteGraph.
        """
        super().__init__(**kwargs)
        self.state_json = state_json
        self._state_to_merge = {}
        if state_json and state_json.strip():
            try:
                extra = json.loads(state_json)
                if not isinstance(extra, dict):
                    raise ValueError(f"state_json must be a JSON object (dictionary), but got {type(extra).__name__}")
                self._state_to_merge = extra
            except json.JSONDecodeError as e:
                logger.error("Failed to parse state_json in __init__: %s", e)
                raise ValueError(f"Invalid JSON provided in state_json: {e}") from e

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Merge pre-parsed JSON properties into the execution state with deep merge for top-level keys."""
        if self._state_to_merge:
            logger.info("WithState: Merging %d keys into state", len(self._state_to_merge))
            return self._state_to_merge
            
        return {}
