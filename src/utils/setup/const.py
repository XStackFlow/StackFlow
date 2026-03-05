from pathlib import Path

# Project root directory (parent of src/)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# Custom graph storage directory
GRAPH_SAVE_PATH = PROJECT_ROOT / "graphs"

# Default output directory for all module artifacts
OUTPUT_DIR = PROJECT_ROOT / "output"

# Session logs directory
SESSION_LOGS_DIR = PROJECT_ROOT / "logs" / "sessions"

# Prompts directory
PROMPTS_DIR = PROJECT_ROOT / "prompts"

# Base URL for the local API server
API_BASE_URL = "http://localhost:8000"

# API paths to exclude from logging (high-frequency polling endpoints)
BLACKLISTED_API_LOGGING = ["/active_sessions"]
