import re
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from src.nodes.abstract.base_node import BaseNode
from src.utils.setup.logger import get_logger

from src.inputs.standard_inputs import Resolvable

logger = get_logger(__name__)


class BranchPreparer(BaseNode):
    """Node that prepares the local repository branch for fixing an issue using a dynamic template."""

    def __init__(
        self,
        repo_name: Resolvable[str] = "{{repo_name}}",
        branch_template: Resolvable[str] = "{{branch_template}}",
        **kwargs
    ):
        """Initialize the branch preparer node.

        Args:
            repo_name: Template string for the repository name (e.g. 'StackAdapt/repo').
            branch_name: Template string for the branch name.
                         Supports {{variable}}, {{state.nested.key}}, etc.
            **kwargs: Additional properties from LiteGraph.
        """
        super().__init__(**kwargs)
        self.repo_name = repo_name
        self.branch_template = branch_template

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic. Sets up a fresh workspace for the branch.

        Args:
            state: Node state containing repo_name and thread_id.

        Returns:
            Updated state with the new repo_path pointing to the symlink.

        Raises:
            ValueError: If required repository information is missing.
        """
        from modules.git.utils import repo_manager
        
        # 1. Access Resolved Inputs
        repo_name = self._repo_name
        if not repo_name:
            raise ValueError("repo_name is required.")

        thread_id = self._thread_id
        if not thread_id:
            raise ValueError("thread_id is required.")

        # 2. Resovle branch_name from template (second resolution)
        from src.utils.template_manager import render_template
        branch_name = render_template(self._branch_template, state)
        if not branch_name:
            raise ValueError("branch_template resolved to an empty string. Please provide a valid branch name or template.")

        # 3. Setup Workspace (Symlink + Clone)
        symlink_name = f"{repo_name.replace('/', '_')}_{thread_id}"
        repo_url = f"https://github.com/{repo_name}.git"
        
        logger.info("Preparing workspace for %s (branch: %s)", repo_name, branch_name)
        
        repo_path = repo_manager.get_or_clone_repository(
            repo_url=repo_url,
            repo_name=symlink_name
        )

        # 4. Checkout and Sync Branch
        repo_manager.checkout_pr_branch(
            repo_path=repo_path.resolve(),
            branch_name=branch_name
        )
        
        return {
            "repo_path": str(repo_path),
            "branch_name": branch_name,
        }

