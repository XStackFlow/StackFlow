from typing import Any, Dict, Optional
import json
from src.nodes.abstract.base_node import BaseNode
from src.utils.setup.logger import get_logger
from src.utils import template_manager

from src.inputs.standard_inputs import JSONString

logger = get_logger(__name__)

class WithStateMapper(BaseNode):
    """Node that maps state values into new state keys using dynamic templates.
    
    Example:
        mapping_json = '{"a": "{{state.test}}"}'
        If state has {"test": "hello"}, the node will add {"a": "hello"} to state.
    """
    
    def __init__(
        self,
        mapping_json: JSONString = "{}",
        **kwargs
    ):
        """Initialize the WithStateMapper node.

        Args:
            mapping_json: A JSON string containing key-template pairs.
            **kwargs: Additional properties from LiteGraph.
        """
        super().__init__(**kwargs)
        self._mapping = {}
        if mapping_json and mapping_json.strip():
            try:
                extra = json.loads(mapping_json)
                if not isinstance(extra, dict):
                    raise ValueError(f"mapping_json must be a JSON object (dictionary), but got {type(extra).__name__}")
                self._mapping = extra
            except json.JSONDecodeError as e:
                logger.error("Failed to parse mapping_json in __init__: %s", e)
                raise ValueError(f"Invalid JSON provided in mapping_json: {e}") from e

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Render templates from mapping and merge into state."""
        if not self._mapping:
            return state

        new_data = {}
        for key, template in self._mapping.items():
            # Use the enhanced render_template which now handles dicts/lists recursively
            new_data[key] = template_manager.render_template(template, state)
        
        if new_data:
            logger.info("Mapping %d keys into state", len(new_data))
            return new_data
            
        return {}

