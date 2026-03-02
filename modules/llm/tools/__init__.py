from modules.llm.tools.git_tools import GIT_TOOLS, GIT_MERGE_TOOLS, git_status, git_diff, git_merge
from modules.llm.tools.go_build_tools import GO_BUILD_TOOLS, go_build, go_test, golangci_lint
from modules.llm.tools.terraform_tools import TERRAFORM_TOOLS, terraform_fmt
from modules.llm.tools.write_tools import WRITE_TOOLS, patch_file, create_file, move_file, copy_file, file_delete
from modules.llm.tools.read_tools import READ_TOOLS, read_file, read_file_segment, list_directory, file_search, search_codebase
from modules.llm.tools.memory_tools import (
    MEMORY_TOOLS,
    query_memory,
    record_memory
)

# Convenience: All tools for code editing tasks
CODE_EDIT_TOOLS = GIT_TOOLS + GO_BUILD_TOOLS + TERRAFORM_TOOLS + WRITE_TOOLS + READ_TOOLS + MEMORY_TOOLS

__all__ = [
    # Tool lists
    "GIT_TOOLS",
    "GIT_MERGE_TOOLS",
    "GO_BUILD_TOOLS",
    "TERRAFORM_TOOLS",
    "WRITE_TOOLS",
    "READ_TOOLS",
    "MEMORY_TOOLS",
    "CODE_EDIT_TOOLS",

    # Git tools
    "git_status",
    "git_diff",
    "git_merge",

    # Go build tools
    "go_build",
    "go_test",
    "golangci_lint",

    # Terraform tools
    "terraform_fmt",

    # Write tools
    "patch_file",
    "create_file",
    "move_file",
    "copy_file",
    "file_delete",

    # Read tools
    "read_file",
    "read_file_segment",
    "list_directory",
    "file_search",
    "search_codebase",

    # Memory tools
    "query_memory",
    "record_memory",
]
