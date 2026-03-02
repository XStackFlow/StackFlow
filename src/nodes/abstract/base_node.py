"""Base node class for all nodes."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict

from functools import wraps
from langfuse import observe
from src.utils.setup.logger import get_logger, log_node_start

logger = get_logger(__name__)


from src.inputs.standard_inputs import Resolvable, resolve_attributes


def observe_node(func):
    """Decorator to observe node execution with Langfuse."""
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        # Use the observe decorator with the node's name
        observed_func = observe(name=self.node_name)(func)
        return await observed_func(self, *args, **kwargs)
    return wrapper


class BaseNode(ABC):
    """Base class for all nodes.
    
    Automatically calls log_node_start when run() is invoked.
    Subclasses should implement _run() instead of run().
    """

    def __init__(self, **kwargs):
        """Initialize the node."""
        super().__init__()
        self._thread_id = None

    @property
    def node_name(self) -> str:
        """Return the name of the node for logging. Defaults to the class name."""
        return self.__class__.__name__

    @property
    def start_message(self) -> str:
        """Return the start message for logging. Includes constructor parameters."""
        # Get instance attributes (constructor parameters)
        params = []
        for key, value in sorted(self.__dict__.items()):
            # Skip private/internal attributes
            if not key.startswith("_"):
                # Format the value nicely
                if isinstance(value, (list, tuple)):
                    value_str = f"[{', '.join(str(v) for v in value)}]"
                elif isinstance(value, Path):
                    value_str = str(value)
                else:
                    value_str = str(value)
                params.append(f"{key}={value_str}")
        
        if params:
            return f"Starting with {', '.join(params)}"
        return "Starting"


    @observe_node
    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Run the node with automatic logging.
        
        Args:
            state: Node state dictionary
            
        Returns:
            Updated state dictionary
        """
        # 1. Identify Context and Namespace
        namespace = state.get("@@namespace")
        
        if namespace:
            namespace_key = namespace
            state_key = f"{namespace_key}@@namespace"
            namespace_state = state.get(state_key, {}) or {}
            
            # Prepare local context: Global State + Namespace-specific overrides
            clean_state = {k: v for k, v in state.items() if not k.endswith("@@namespace") and k != "@@namespace"}
            local_context = {**clean_state, **namespace_state}
        else:
            local_context = state

        # 1b. Capture Thread ID
        self._thread_id = local_context.get("thread_id") or state.get("thread_id")

        # 2. Resolve templates in constructor parameters based on the identified context
        resolve_attributes(self, local_context)
        
        # 3. Helper for execution
        import inspect
        async def execute_logic(ctx):
            if inspect.iscoroutinefunction(self._run):
                return await self._run(ctx)
            else:
                import asyncio
                return await asyncio.to_thread(self._run, ctx)

        # 4. Handle Execution
        if namespace:
            from src.utils.setup.logger import namespace_scope
            
            # Set logger context to distinguish parallel branch logs via context manager
            with namespace_scope(namespace_key):
                log_node_start(self.node_name, self.start_message)
                
                # Execute the node logic
                result = await execute_logic(local_context)
                
                # Merge results into the existing namespace state to avoid wipes.
                updated_namespace = {**namespace_state}
                for k, v in result.items():
                    if k.endswith("@@namespace") or k == "@@namespace":
                        continue
                    
                    # Only update if the key is new or its value has changed from the local context
                    if k not in local_context or result[k] != local_context[k]:
                        updated_namespace[k] = v
                
                return {state_key: updated_namespace}
                
        # Non-namespaced: Return the result directly.
        log_node_start(self.node_name, self.start_message)
        return await execute_logic(state)

    @abstractmethod
    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic. Implemented by subclasses."""
        pass
