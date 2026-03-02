"""Core node packages for StackFlow.

Nodes are organized into subdirectories:
- abstract/: Base interfaces and abstract classes
- common/: Shared utility nodes (always loaded)

Module nodes (jira, slack, github, aws, git, llm, pr) live in modules/
and are loaded on demand via the package manager.
"""

from src.nodes import abstract
from src.nodes import common
