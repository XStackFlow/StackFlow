# LLM module


def on_startup() -> None:
    """Sync and rehydrate the memory index on server startup."""
    from modules.llm.utils.memory_manager import get_memory_manager
    get_memory_manager().sync_memory()
