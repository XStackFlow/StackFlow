"""LLM Repo Agent Node - Generic node for executing repository tasks based on Langfuse prompts."""

from typing import Any, Dict, Tuple, Optional, List
from modules.llm.llm_repo_executor import LLMRepoExecutor
from src.utils.setup.langfuse_helper import get_langfuse_client
from src.utils.setup.logger import get_logger
from src.inputs.standard_inputs import Resolvable
from modules.llm.inputs import Model, Prompt, ToolSets

logger = get_logger(__name__)

class LLMRepoAgent(LLMRepoExecutor):
    """Generic node that executes automated repository tasks using an LLM and a Langfuse prompt.
    
    This node fetches a versioned prompt from Langfuse, compiles it with the current
    state variables, and executes it using the LLMRepoExecutor framework.
    """

    def __init__(
        self, 
        model: Resolvable[Model] = "ollama|qwen2.5:32b",
        temperature: Resolvable[float] = "0.0",
        prompt_name: Resolvable[Prompt] = "PRFixer", 
        recursion_limit: Resolvable[int] = "25",
        tool_sets: Resolvable[ToolSets] = ["CODE_EDIT_TOOLS"],
        required_keys: Resolvable[List[str]] = [],
        repo_path: Resolvable[str] = "{{repo_path}}",
        **kwargs
    ):
        """Initialize the LLM agent.

        Args:
            model: Combined provider and model name (e.g., 'ollama|qwen2.5:32b', 'google|gemini-3-flash-preview').
            temperature: Sampling temperature.
            prompt_name: Name of the prompt in Langfuse to use.
            recursion_limit: Maximum number of tool-use steps.
            tool_sets: List of tool set names (e.g. ['READ_TOOLS', 'WRITE_TOOLS'])
            required_keys: List of keys that must be present in the LLM response.
            repo_path: The local repository path (template supported).
            **kwargs: Additional properties from LiteGraph.
        """
        super().__init__(repo_path=repo_path, **kwargs)
        self.model = model
        self.temperature = temperature
        self.prompt_name = prompt_name
        self.recursion_limit = recursion_limit
        self.tool_sets = tool_sets
        self.required_keys = required_keys

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the agent task with validation of resolved attributes."""
        # 1. Validation of resolved attributes (only available during _run)
        if not self._model:
            raise ValueError("model is required")
        
        if self._temperature is None:
            raise ValueError("temperature is required")
        
        if not self._prompt_name:
            raise ValueError("prompt_name is required")
        
        if not self._recursion_limit:
            raise ValueError("recursion_limit is required")

        logger.info(
            "%s: Running task with model=%s, prompt=%s, temp=%s, recursion_limit=%s, tools=%s, required_keys=%s", 
            self.node_name, self._model, self._prompt_name, self._temperature, 
            self._recursion_limit, self._tool_sets, self._required_keys
        )

        return super()._run(state)

    def get_model(self, state: Dict[str, Any]) -> Tuple[str, str, float]:
        """Return the model to use, favoring the constructor parameters."""
        if self._model and "|" in self._model:
            # First part is provider, everything after is the model name/path
            parts = self._model.split("|", 1)
            provider = parts[0]
            model_name = parts[1]
            return (provider, model_name, self._temperature)
        
        # Error if model is not correctly set as provider|model
        raise ValueError(f"Invalid model configuration: '{self._model}'. Expected format: 'provider|model_name'")

    def get_tools(self, state: Dict[str, Any]) -> list:
        """Return the specific set of tools resolved from tool sets."""
        import modules.llm.tools as tools_module
        
        resolved_tools = []
        seen_tool_names = set()
        
        if not self._tool_sets:
            return []
            
        for set_name in self._tool_sets:
            tool_list = getattr(tools_module, set_name, [])
            if isinstance(tool_list, list):
                for tool in tool_list:
                    if tool.name not in seen_tool_names:
                        resolved_tools.append(tool)
                        seen_tool_names.add(tool.name)
        
        return resolved_tools


    def get_content(self, state: Dict[str, Any]) -> str:
        """Fetch and compile the prompt from Langfuse."""
        client = get_langfuse_client()
        prompt_obj = client.get_prompt(self._prompt_name)

        # Serialize list values into strings for prompt injection.
        # Lists of {"role", "content"} dicts become "Role: content" lines;
        # other lists become newline-joined string representations.
        compiled_state = {}
        for k, v in state.items():
            if isinstance(v, list):
                if v and all(isinstance(m, dict) and "role" in m and "content" in m for m in v):
                    compiled_state[k] = "\n".join(f"{m['role']}: {m['content']}" for m in v)
                else:
                    compiled_state[k] = "\n".join(str(item) for item in v)
            else:
                compiled_state[k] = v

        return prompt_obj.compile(**compiled_state)

    def next_state(self, state: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        """Update state by returning the LLM JSON result delta."""
        if isinstance(result, dict):
            return result
        return {}
