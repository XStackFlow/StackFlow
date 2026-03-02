"""LLM Repo Executor - Base class for nodes that execute automated tasks using various LLM providers with a repository context."""

import os
from abc import abstractmethod
from pathlib import Path
from typing import Any, Dict, Tuple, Optional
from src.inputs.standard_inputs import Resolvable

from src.nodes.abstract.base_node import BaseNode
from modules.llm.utils.cursor import execute_cursor
from modules.llm.utils.gemini import execute_gemini
from modules.llm.utils.langchain import execute_langchain
from src.utils.setup.logger import get_logger
from modules.llm.utils.llm_utils import initialize_llm
from src.utils.setup.config_registry import get_configuration_type
from src.utils.exceptions import RetriableError

logger = get_logger(__name__)


def _resolve_provider_type(provider_name: str) -> str:
    """Resolve a provider name to its execution type via the configuration registry.

    Raises:
        ConfigurationError: If the provider is not registered.
    """
    from src.utils.exceptions import ConfigurationError
    ptype = get_configuration_type("llm", provider_name)
    if not ptype:
        raise ConfigurationError(f"Provider '{provider_name}' not found in configurations")
    return ptype


class LLMRepoExecutor(BaseNode):
    """Base class for executing LLM-based tasks using various providers.

    Supports:
    - cursor: Executes using Cursor CLI (chat)
    - gemini: Executes using Gemini CLI
    - ollama: Executes using LangChain ChatOllama
    - openai: Executes using LangChain ChatOpenAI
    - google: Executes using LangChain ChatGoogleGenerativeAI
    """

    def __init__(self, repo_path: Resolvable[str] = "{{repo_path}}", **kwargs):
        """Initialize the node."""
        super().__init__(**kwargs)
        self.repo_path = repo_path
        self._llm = None
        self._provider = None
        self._model_name = None
        self._temperature = None
        self._langfuse_handler = None

    @abstractmethod
    def get_model(self, state: Dict[str, Any]) -> Tuple[str, str, float]:
        """Return the model specification as (provider, model_name, temperature)."""
        pass

    def _get_llm(self, state: Dict[str, Any]) -> Any:
        """Lazy initialization of the LangChain LLM."""
        provider, model_name, temperature = self.get_model(state)

        # Re-initialize if provider, model name, or temperature has changed
        if (self._llm is not None and
            self._provider == provider and
            self._model_name == model_name and
            self._temperature == temperature):
            return self._llm

        ptype = _resolve_provider_type(provider)
        if ptype in ("cursor_cli", "gemini_cli"):
            return None

        self._llm = initialize_llm(provider, model_name, temperature)
        self._provider = provider
        self._model_name = model_name
        self._temperature = temperature
        return self._llm

    @abstractmethod
    def get_content(self, state: Dict[str, Any]) -> str:
        """Return the content/instructions for the task."""
        pass

    def get_tools(self, state: Dict[str, Any]) -> list:
        """Return the list of tools to provide to the LLM agent.

        Defaults to an empty list. Subclasses should override this to provide
        the specific tools they require.
        """
        return []

    @property
    def langfuse_handler(self) -> Any:
        """Return the Langfuse callback handler if configured."""
        if self._langfuse_handler is None and os.getenv("LANGFUSE_PUBLIC_KEY"):
            from langfuse.langchain import CallbackHandler
            self._langfuse_handler = CallbackHandler()
        return self._langfuse_handler

    def get_step_name(self, state: Dict[str, Any]) -> str:
        """Return the name of the step to set in state after completion."""
        return "llm_task_completed"

    def next_state(self, state: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        """Update state based on task execution result. Defaults to no change."""
        return {}

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the task using the appropriate provider."""
        repo_path = self._repo_path
        if not repo_path:
            raise ValueError("repo_path is required")
        repo_path = Path(repo_path)
        if not repo_path.exists():
            raise ValueError(f"Repository path does not exist: {repo_path}")

        content = self.get_content(state)
        provider, model_name, temperature = self.get_model(state)
        tools = self.get_tools(state)

        ptype = _resolve_provider_type(provider)
        logger.info("Executing task using provider '%s' (type=%s) and model '%s' (temp: %s)", provider, ptype, model_name, temperature)

        if ptype == "cursor_cli":
            result = execute_cursor(
                repo_path, content, tools=tools, model=model_name
            )
        elif ptype == "gemini_cli":
            result = execute_gemini(
                repo_path, content, tools=tools, model=model_name
            )
        else:
            llm = self._get_llm(state)
            result = execute_langchain(
                llm=llm,
                repo_path=repo_path,
                content=content,
                tools=tools,
                recursion_limit=self._recursion_limit,
                callbacks=[self.langfuse_handler] if self.langfuse_handler else None,
                run_name=self.__class__.__name__
            )

            # Flush langfuse traces to ensure they are sent before the process potentially exits
            if self.langfuse_handler and hasattr(self.langfuse_handler, "langfuse"):
                try:
                    self.langfuse_handler.langfuse.flush()
                except Exception as e:
                    logger.warning("Failed to flush Langfuse traces: %s", e)

        logger.info("Task result: %s", result)
        logger.info("Required keys: %s", self._required_keys)

        if isinstance(result, dict) and result.get("status") == "failed":
            message = result.get("message", "Task execution failed")
            logger.error(" task failed: %s", message)
            raise RetriableError(message)

        # Validate required keys are present in the response
        if self._required_keys:
            if not isinstance(result, dict):
                error_msg = f"LLM response must be a dictionary when required_keys are specified, but got {type(result).__name__}"
                logger.error("%s. Result: %s", error_msg, result)
                raise RetriableError(error_msg)

            missing_keys = [key for key in self._required_keys if key not in result]
            if missing_keys:
                error_msg = f"LLM response missing required keys: {', '.join(missing_keys)}"
                logger.error("%s. Result: %s", error_msg, result)
                raise RetriableError(error_msg)

        updated_state = self.next_state(state, result)

        return {
            **updated_state,
        }
