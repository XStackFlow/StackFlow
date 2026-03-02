from typing import Any, Dict
from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class DelayNode(BaseNode):
    """Node that pauses execution for a specified duration."""

    def __init__(self, delay_seconds: Resolvable[float] = "{{delay_seconds}}", **kwargs):
        """Initialize the delay node.
        
        Args:
            delay_seconds: Number of seconds to pause (ResolvableFloat).
            **kwargs: Additional keyword arguments for the base class.
        """
        super().__init__(**kwargs)
        self.delay_seconds = delay_seconds

    async def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic (pauses execution)."""
        import asyncio
        
        # self.delay_seconds is already resolved and cast to float by BaseNode
        logger.info(f"DelayNode: Pausing for {self._delay_seconds} seconds...")
        await asyncio.sleep(self._delay_seconds)
        logger.info("DelayNode: Resuming execution.")
        
        return {}
