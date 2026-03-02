"""Graph Executor — launches a graph via the API after user confirmation."""

import random
import string
from typing import Any, Dict

import requests

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger
from src.utils.setup.const import API_BASE_URL

logger = get_logger(__name__)


class GraphExecutor(BaseNode):
    """Node that triggers graph execution via the API."""

    def __init__(
        self,
        graph_name: Resolvable[str] = "{{graph_name}}",
        graph_params: Resolvable[dict] = "{{graph_params}}",
        **kwargs
    ):
        super().__init__(**kwargs)
        self.graph_name = graph_name
        self.graph_params = graph_params

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        graph_name = self._graph_name
        params = self._graph_params or {}

        if not graph_name:
            raise ValueError("No graph_name provided")

        # Generate a unique session id
        session_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        thread_id = f"{graph_name}_{session_id}"

        payload = {
            "root_graph_id": graph_name,
            "thread_id": thread_id,
            "params": params,
        }

        logger.info("GraphExecutor: Launching %s (thread_id=%s) with params=%s", graph_name, thread_id, params)

        response = requests.post(f"{API_BASE_URL}/execute", json=payload, timeout=15)
        response.raise_for_status()
        logger.info("GraphExecutor: API response: %s", response.json())

        return {"launched_session_id": session_id}
