import json
from typing import Any, Dict, Optional, Tuple

from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from src.utils.exceptions import ConfigurationError, RetriableError
from src.utils.setup.logger import get_logger
from src.utils.setup.langfuse_helper import get_langfuse_client

logger = get_logger(__name__)

# Cache for system message
_system_message_cache = None

def get_system_message() -> str:
    """Load system-level instructions from Langfuse SYSTEM prompt (cached).

    Returns:
        System message content, or empty string if prompt doesn't exist.
    """
    global _system_message_cache

    if _system_message_cache is not None:
        return _system_message_cache

    try:
        client = get_langfuse_client()
        prompt_obj = client.get_prompt("llm/SYSTEM")
        _system_message_cache = prompt_obj.prompt
        return _system_message_cache
    except Exception as e:
        # If SYSTEM prompt doesn't exist, cache empty string
        logger.warning("No SYSTEM prompt found in Langfuse: %s", e)
        return None

def parse_llm_json(text: str) -> Dict[str, Any]:
    """Parse JSON from LLM output, finding the last valid JSON block.

    This handles potential conversational wrappers, preambles, and multiple
    JSON blocks by searching for candidates and returning the last valid one.

    Args:
        text: Raw text output from the LLM.

    Returns:
        Parsed JSON dictionary.

    Raises:
        RetriableError: If no valid JSON could be extracted or parsed.
    """
    text = text.strip()

    # Try parsing the whole thing first
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        first_error = e

    # Find all positions of '{' and '}'
    starts = [i for i, char in enumerate(text) if char == '{']
    ends = [i for i, char in enumerate(text) if char == '}']

    if starts and ends:
        # Search for the last valid JSON block
        # We start from the last closing brace and find the nearest opening brace
        # that results in valid JSON.
        for end_pos in reversed(ends):
            # Search opening braces from right to left (relative to the end_pos)
            possible_starts = [s for s in starts if s < end_pos]
            for start_pos in reversed(possible_starts):
                try:
                    candidate = text[start_pos:end_pos + 1]
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue

    # If we get here, no valid JSON block was found
    raise RetriableError(f"Failed to parse LLM output as JSON: {first_error}\nOutput: {text[:500]}...")


def initialize_llm(provider_name: str, model_name: str, temperature: float) -> Any:
    """Initialize a LangChain LLM by looking up the provider in providers.json.

    CLI-only types (cursor_cli, gemini_cli) cannot be initialized as LangChain
    objects and will raise ConfigurationError.

    Raises:
        ConfigurationError: If the provider is not found in configurations.
    """
    from src.utils.setup.config_registry import get_configuration

    prov = get_configuration("llm", provider_name)
    if not prov:
        raise ConfigurationError(f"LLM provider '{provider_name}' not found in configurations")

    ptype = prov["type"]
    opts = prov.get("options") or {}

    return _initialize_by_type(ptype, opts, model_name, temperature, label=provider_name)


def _initialize_by_type(ptype: str, opts: dict, model_name: str, temperature: float, label: str) -> Any:
    """Create a LangChain LLM instance based on provider type and options."""

    if ptype == "openai":
        api_key = opts.get("API_KEY") or ""
        base_url = opts.get("BASE_URL") or "https://api.openai.com/v1"
        if not api_key:
            logger.warning("Provider '%s': no API_KEY set — request will likely fail unless the endpoint is auth-free", label)
            api_key = "none"
        logger.info("Initializing OpenAI LLM (provider=%s) model=%s url=%s temp=%s", label, model_name, base_url, temperature)
        return ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            name=f"OpenAI:{model_name}"
        )

    elif ptype == "ollama":
        base_url = opts.get("BASE_URL") or "http://localhost:11434"
        logger.info("Initializing Ollama LLM (provider=%s) model=%s url=%s temp=%s", label, model_name, base_url, temperature)
        return ChatOllama(
            model=model_name,
            base_url=base_url,
            temperature=temperature,
            name=f"Ollama:{model_name}"
        )

    elif ptype == "google_vertex_ai":
        project = opts.get("PROJECT")
        location = opts.get("LOCATION") or "global"
        logger.info("Initializing Vertex AI LLM (provider=%s) model=%s project=%s location=%s temp=%s", label, model_name, project, location, temperature)
        return ChatGoogleGenerativeAI(
            model=model_name,
            location=location,
            temperature=temperature,
            project=project,
            name=f"Google:{model_name}"
        )

    elif ptype == "cursor_cli":
        raise ConfigurationError(f"Provider '{label}' (cursor_cli) does not support LangChain initialization. It is executed via CLI.")

    elif ptype == "gemini_cli":
        raise ConfigurationError(f"Provider '{label}' (gemini_cli) does not support LangChain initialization. It is executed via CLI.")

    else:
        raise ConfigurationError(f"Unsupported provider type '{ptype}' for provider '{label}'")
