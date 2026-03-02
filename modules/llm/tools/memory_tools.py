from typing import List, Optional, Literal
import datetime
from langchain_core.tools import tool
from src.utils.setup.logger import get_logger
from modules.llm.utils.memory_manager import get_memory_manager
from modules.llm.const import MEMORY_LIMIT_MAX

logger = get_logger(__name__)

@tool
def query_memory(query: str, limit: int = 5) -> str:
    """
    Search the long-term memory for rules, lessons, and architectural patterns.

    Args:
        query: The semantic search query (e.g. \"How do I fix the auth bug?\").
        limit: The number of relevant chunks to return (1-20). Default is 5.
               - Use 3 for simple rule lookups.
               - Use 10+ for deep debugging or understanding new patterns.
    """
    manager = get_memory_manager()
    safe_limit = max(1, min(limit, MEMORY_LIMIT_MAX))
    results = manager.search(query, limit=safe_limit)

    if not results or (isinstance(results, list) and "Error" in results[0]):
        return "No relevant memories found."

    return f"Found {len(results)} relevant memories:\n\n" + "\n---\n".join(results)

@tool
def record_memory(
    content: str,
    scope: Literal["repo", "global"],
    category: str,
    repo_name: str = ""
) -> str:
    """
    Records a new insight or architectural decision into persistent memory.

    Args:
        content: The content/lesson to record. Try to be concise and actionable.
        scope: Whether this applies to the specific 'repo' or is 'global' across all projects.
        category: The topic or file name (e.g. 'architecture', 'lessons', 'patterns').
                  Avoid overly specific names; prefer generic categories for better searchability.
        repo_name: The full name of the repository (e.g. 'stackadapt-infra', 'stackadapt-config', 'stackadapt-go-segment').
                  Always use the 'stackadapt-' prefix for internal repositories.
                  REQUIRED if scope is 'repo'.
    """
    manager = get_memory_manager()
    base_dir = manager.repo_path if scope == "repo" else manager.global_path

    # Sanitize and normalize inputs
    clean_repo = repo_name.replace("/", "-").lower() if repo_name else ""

    # Normalize known repositories to include 'stackadapt-' prefix
    known_repos = {"infra", "config", "access", "go-segment"}
    if clean_repo in known_repos:
        clean_repo = f"stackadapt-{clean_repo}"

    clean_cat = category.replace("/", "-").lower()
    if clean_cat.endswith(".md"):
        clean_cat = clean_cat[:-3]

    # Build path: memory/{scope}/[repo_name]/{category}.md
    if scope == "repo" and clean_repo:
        file_path = base_dir / clean_repo / f"{clean_cat}.md"
    else:
        file_path = base_dir / f"{clean_cat}.md"

    # Ensure directory exists
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Check if file exists to determine if we need a title
    file_exists = file_path.exists()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d")

    # Prepend Section Header if it doesn't look like one is provided
    formatted_content = content.strip()
    if not formatted_content.startswith("#"):
        formatted_content = f"#### Added on {timestamp}\n{formatted_content}"

    try:
        with open(file_path, "a", encoding="utf-8") as f:
            if not file_exists:
                # Initialize new file with a Title
                repo_title = f" [{repo_name}]" if repo_name else ""
                title = f"# {category.title()}{repo_title}"
                f.write(f"{title}\n")

            f.write(f"\n\n{formatted_content}\n")

        # Trigger an immediate sync to make it searchable
        manager.sync_memory()

        prefix = f"{scope}/{clean_repo}/" if clean_repo else f"{scope}/"
        return f"Successfully recorded memory in {prefix}{clean_cat}.md"
    except Exception as e:
        logger.error("Error recording memory: %s", e)
        return f"Error recording memory: {str(e)}"

# exported tool list
MEMORY_TOOLS = [query_memory, record_memory]
