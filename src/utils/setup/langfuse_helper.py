import os
from src.utils.setup.const import PROMPTS_DIR
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

def register_prompts(langfuse_client):
    """
    Reads all .md files in the prompts directory and registers them in Langfuse.
    """
    if not PROMPTS_DIR.exists():
        logger.warning("Prompts directory %s does not exist.", PROMPTS_DIR)
        return

    logger.info("─" * 60)
    logger.info("📡 SYNCING LANGFUSE PROMPTS...")
    logger.info("─" * 60)
    
    for prompt_file in PROMPTS_DIR.rglob("*.md"):
        # Create a name based on the relative path to support nested structures
        relative_path = prompt_file.relative_to(PROMPTS_DIR)
        prompt_name = str(relative_path.with_suffix('')).replace(os.sep, "/")
        
        try:
            with open(prompt_file, "r") as f:
                content = f.read().strip().replace("\r\n", "\n")
            
            # 1. Fetch the latest version (no label filter) to see what is actually in Langfuse
            try:
                # get_prompt with no label/version returns the latest version
                latest_prompt = langfuse_client.get_prompt(prompt_name)
                # In some SDK versions, prompt content is in .prompt, in others it might be slightly different
                latest_content = str(latest_prompt.prompt).strip().replace("\r\n", "\n")
                
                # Detailed comparison
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

            # 2. Create the prompt version with the 'production' label
            langfuse_client.create_prompt(
                name=prompt_name,
                prompt=content,
                type="text",
                labels=["production"]
            )
            logger.info("  %-20s | ✅ DEPLOYED (New Version)", prompt_name)
        except Exception as e:
            logger.error("  %-20s | ❌ FAILED: %s", prompt_name, e)
    
    logger.info("─" * 60)
