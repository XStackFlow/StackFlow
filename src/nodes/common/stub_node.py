"""Stub Node - A node that does nothing, used for testing or placeholders."""

from typing import Any, Dict
from src.nodes.abstract.base_node import BaseNode
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class StubNode(BaseNode):
    """Node that does nothing and passes state through."""

    def __init__(self, **kwargs):
        """Initialize the stub node."""
        super().__init__(**kwargs)

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic (do nothing)."""
        logger.info("StubNode executing: doing nothing.")
        return {}
