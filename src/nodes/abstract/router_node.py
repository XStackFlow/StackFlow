"""Router node class for branching logic."""

from abc import abstractmethod
from typing import Any, Dict, List
from src.nodes.abstract.base_node import BaseNode


class RouterNode(BaseNode):
    """Abstract class for nodes that perform routing/branching logic.
    
    Subclasses must implement:
    - get_route_options: Returns a list of all possible routing strings.
    - get_route: Returns exactly one of the options from get_route_options based on state.
    """

    @abstractmethod
    def get_route_options(self) -> List[str]:
        """Return all possible routing options."""
        pass

    @abstractmethod
    def get_route(self, state: Dict[str, Any]) -> str:
        """Return the chosen route based on current state."""
        pass

    async def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Router nodes typically just calculate the next step name.
        
        Subclasses can override this if they need to update state,
        but must ensure the 'next_step' is set to the result of get_route().
        """
        import inspect
        if inspect.iscoroutinefunction(self.get_route):
            route = await self.get_route(state)
        else:
            route = self.get_route(state)

        if route not in self.get_route_options():
            raise ValueError(f"Invalid route returned by {self.node_name}: {route}. "
                             f"Must be one of {self.get_route_options()}")

        return {
            "next_step": route
        }
