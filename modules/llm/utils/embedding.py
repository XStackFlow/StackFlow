from typing import Any
from src.utils.exceptions import ConfigurationError
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


def initialize_embedding(provider_name: str, model_name: str) -> Any:
    """Initialize a LangChain Embedding model by looking up the provider in configurations.

    Args:
        provider_name: Name of a registered provider (e.g. "ollama", "google").
        model_name: The embedding model to use (e.g. "nomic-embed-text-v2-moe").

    Raises:
        ConfigurationError: If the provider is not found or its type is unsupported for embeddings.
    """
    from src.utils.setup.config_registry import get_configuration

    prov = get_configuration("llm", provider_name)
    if not prov:
        raise ConfigurationError(f"Embedding provider '{provider_name}' not found in configurations")

    ptype = prov["type"]
    opts = prov.get("options") or {}

    if ptype == "ollama":
        from langchain_ollama import OllamaEmbeddings
        base_url = opts.get("BASE_URL") or "http://localhost:11434"
        logger.info("Initializing Ollama Embeddings with model: %s at %s", model_name, base_url)
        return OllamaEmbeddings(model=model_name, base_url=base_url)

    elif ptype == "openai":
        from langchain_openai import OpenAIEmbeddings
        api_key = opts.get("API_KEY")
        if not api_key:
            raise ConfigurationError(f"Provider '{provider_name}': API_KEY is required for openai embeddings")
        logger.info("Initializing OpenAI Embeddings with model: %s", model_name)
        return OpenAIEmbeddings(model=model_name, api_key=api_key)

    elif ptype == "google_vertex_ai":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        logger.info("Initializing Google Generative AI Embeddings with model: %s", model_name)
        return GoogleGenerativeAIEmbeddings(model=model_name)

    else:
        raise ConfigurationError(
            f"Provider type '{ptype}' (provider '{provider_name}') does not support embeddings. "
            f"Supported types: ollama, openai, google_vertex_ai"
        )
