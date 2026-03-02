import os
from pathlib import Path
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)

_client = None


def get_langfuse_client():
    """Get or initialize the global Langfuse client."""
    global _client
    if _client is None:
        from langfuse import Langfuse
        _client = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_HOST"),
        )
    return _client


def _collect_prompt_dirs() -> list[tuple[Path, str]]:
    """Return (prompts_dir, module_id) pairs for each installed module that ships prompts."""
    dirs = []
    from src.utils.setup.module_registry import _iter_module_dirs
    for module_dir, _pkg in _iter_module_dirs():
        prompts_dir = module_dir / "prompts"
        if prompts_dir.exists():
            dirs.append((prompts_dir, module_dir.name))
    return dirs


def _get_langfuse_production_prompts(langfuse_client) -> set[str]:
    """Fetch all prompt names with 'production' label from Langfuse."""
    names = set()
    page = 1
    while True:
        result = langfuse_client.api.prompts.list(page=page, limit=50)
        for p in result.data:
            labels = p.labels or []
            # labels is a list of lists or list of strings depending on version
            flat = []
            for l in labels:
                if isinstance(l, list):
                    flat.extend(l)
                else:
                    flat.append(l)
            if "production" in flat:
                names.add(p.name)
        if len(result.data) < 50:
            break
        page += 1
    return names


def _cleanup_stale_prompts(langfuse_client, current_names: set[str]) -> None:
    """Archive Langfuse prompts with 'production' label that are no longer on disk."""
    remote_names = _get_langfuse_production_prompts(langfuse_client)
    stale = remote_names - current_names
    if not stale:
        return

    for prompt_name in sorted(stale):
        try:
            langfuse_client.create_prompt(
                name=prompt_name,
                prompt="[removed]",
                type="text",
                labels=["archived"]
            )
            logger.info("  %-20s | 🗑️  ARCHIVED (no longer on disk)", prompt_name)
        except Exception as e:
            logger.warning("  %-20s | ⚠️  Failed to archive: %s", prompt_name, e)


def register_prompts(langfuse_client):
    """
    Reads all .md files from each installed module's prompts/ directory and
    registers them in Langfuse, namespaced as '{module_id}/{stem}'.
    Namespaced prompts that exist in Langfuse but no longer on disk are archived.
    """
    prompt_dirs = _collect_prompt_dirs()

    logger.info("─" * 60)
    logger.info("📡 SYNCING LANGFUSE PROMPTS...")
    logger.info("─" * 60)

    current_names = set()

    for prompts_dir, module_id in prompt_dirs:
        for prompt_file in prompts_dir.rglob("*.md"):
            relative_path = prompt_file.relative_to(prompts_dir)
            stem = str(relative_path.with_suffix("")).replace(os.sep, "/")
            prompt_name = f"{module_id}/{stem}"
            current_names.add(prompt_name)

            try:
                with open(prompt_file, "r") as f:
                    content = f.read().strip().replace("\r\n", "\n")

                # 1. Fetch the latest version to compare with local file
                try:
                    latest_prompt = langfuse_client.get_prompt(prompt_name)
                    latest_content = str(latest_prompt.prompt).strip().replace("\r\n", "\n")

                    content_matches = (latest_content == content)
                    current_labels = latest_prompt.labels or []
                    has_production_label = "production" in current_labels

                    if content_matches and has_production_label:
                        logger.info("✅ Prompt '%s' (v%s) is already up to date and production-ready.",
                                    prompt_name, latest_prompt.version)
                        continue

                    if content_matches:
                        logger.info("⚠️ Prompt '%s' (v%s) content matches, but 'production' label is missing (Labels: %s).",
                                    prompt_name, latest_prompt.version, current_labels)
                    else:
                        logger.info("🔄 Prompt '%s' (v%s) content mismatch. File len: %d, Langfuse len: %d. Labels: %s",
                                    prompt_name, latest_prompt.version, len(content), len(latest_content), current_labels)
                except Exception as e:
                    if "404" in str(e) or "LangfuseNotFoundError" in str(e):
                        logger.info("Initializing first version for prompt '%s'...", prompt_name)
                    else:
                        logger.warning("Error fetching latest version of prompt '%s': %s", prompt_name, e)

                # 2. Create/update the prompt with the 'production' label
                langfuse_client.create_prompt(
                    name=prompt_name,
                    prompt=content,
                    type="text",
                    labels=["production"]
                )
                logger.info("  %-20s | ✅ DEPLOYED (New Version)", prompt_name)
            except Exception as e:
                logger.error("  %-20s | ❌ FAILED: %s", prompt_name, e)

    # Archive namespaced prompts in Langfuse that no longer exist on disk
    _cleanup_stale_prompts(langfuse_client, current_names)

    logger.info("─" * 60)
