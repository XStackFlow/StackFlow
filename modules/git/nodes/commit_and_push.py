"""Commit and push local changes to the current branch."""

from typing import Any, Dict

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger
from modules.git.utils.repo_manager import commit_and_push_changes

logger = get_logger(__name__)


class CommitAndPush(BaseNode):
    """Node that stages, commits, and pushes all local changes to the current branch."""

    def __init__(
        self,
        repo_path: Resolvable[str] = "{{repo_path}}",
        commit_message: Resolvable[str] = "{{commit_message}}",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.repo_path = repo_path
        self.commit_message = commit_message

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        repo_path = self._repo_path
        if not repo_path:
            raise ValueError("repo_path is required")

        commit_message = self._commit_message or "Fix PR issues"
        commit_and_push_changes(repo_path, commit_message, new_branch=None, allow_empty=False)
        return {}
