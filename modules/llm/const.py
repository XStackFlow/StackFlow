from typing import Final
from src.utils.setup.const import PROJECT_ROOT

# --- Read tools ---
READ_FILE_MAX_LINES: Final = 300
SEARCH_CODEBASE_MAX_RESULTS: Final = 100

# --- Memory storage ---
MEMORY_DIR = PROJECT_ROOT / "memory"
MEMORY_EMBEDDING_MODEL = ("ollama", "nomic-embed-text-v2-moe")

# --- Memory retrieval tuning ---
CANDIDATE_MULTIPLIER = 2        # Multiplier for oversampling candidates
MAX_FETCH_LIMIT = 30            # Absolute ceiling for candidate pool size
MEMORY_TOKEN_BUDGET = 2000      # Max tokens to return in memory context
MEMORY_RELEVANCE_THRESHOLD = 0.5  # Filter out irrelevant vector matches (distance threshold)
MEMORY_HYBRID_WEIGHTS = [0.3, 0.7]  # OpenClaw Strategy: 30% Keyword (BM25), 70% Vector (Semantic)
MEMORY_LIMIT_MAX = 20           # Max number of chunks to return even if budget allows more
