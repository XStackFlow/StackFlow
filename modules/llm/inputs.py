from typing import Annotated, List

AVAILABLE_MODELS = [
    "ollama|qwen2.5:32b",
    "ollama|gemma3:12b",
    "openai|google/gemini-3-flash-preview",
    "cursor|composter-1",
    "gemini|gemini-3-flash-preview",
    "google|gemini-3-flash-preview",
]

AVAILABLE_TOOL_SETS = [
    "READ_TOOLS",
    "WRITE_TOOLS",
    "GIT_TOOLS",
    "GIT_MERGE_TOOLS",
    "GO_BUILD_TOOLS",
    "MEMORY_TOOLS",
    "TERRAFORM_TOOLS",
    "CODE_EDIT_TOOLS",
]

Model = Annotated[str, AVAILABLE_MODELS]
ToolSets = Annotated[List[str], AVAILABLE_TOOL_SETS]

LANGFUSE_PROMPTS_URL = "http://localhost:3000/project/stackflow/prompts"
Prompt = Annotated[str, LANGFUSE_PROMPTS_URL]
