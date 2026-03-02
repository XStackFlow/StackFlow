"""PR Creator  - Creates GitHub pull requests using gh CLI."""

import re
import subprocess
from pathlib import Path
from typing import Any, Dict

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger
from modules.git.utils.repo_manager import commit_and_push_changes

logger = get_logger(__name__)

class PRCreator(BaseNode):
    """Node that creates GitHub pull requests using gh CLI."""

    def __init__(
        self,
        branch_name: Resolvable[str] = "{{branch_name}}",
        pr_title: Resolvable[str] = "{{pr_title}}",
        pr_body: Resolvable[str] = "{{pr_body}}",
        repo_path: Resolvable[str] = "{{repo_path}}",
        **kwargs
    ):
        """Initialize the PRCreator node.

        Args:
            branch_name: Name of the branch to create PR for (template supported).
            pr_title: Title of the pull request (template supported).
            pr_body: Body of the pull request (template supported).
            repo_path: Path to the local repository (template supported).
            **kwargs: Additional properties from LiteGraph.
        """
        super().__init__(**kwargs)
        self.branch_name = branch_name
        self.pr_title = pr_title
        self.pr_body = pr_body
        self.repo_path = repo_path

    def _create_github_pr(
        self,
        repo_path: Path,
        branch_name: str,
        pr_title: str,
        pr_body: str,
    ) -> Dict[str, Any]:
        """Create a GitHub pull request using gh CLI.

        Args:
            repo_path: Path to the local repository
            branch_name: Name of the branch to create PR for
            pr_title: Title of the pull request
            pr_body: Body of the pull request

        Returns:
            Dictionary with PR creation result

        Raises:
            RuntimeError: If PR creation fails
        """
        try:
            formatted_body = pr_body or ""

            # Create PR using gh CLI
            cmd = [
                "gh",
                "pr",
                "create",
                "--head",
                branch_name,
                "--title",
                pr_title,
                "--body",
                formatted_body,
            ]

            result = subprocess.run(
                cmd,
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                raise RuntimeError(f"Failed to create PR: {result.stderr}")

            # Parse PR URL from output (usually in format "https://github.com/owner/repo/pull/123")
            output = result.stdout.strip()
            pr_url_match = re.search(r"https://github\.com/[^\s]+", output)
            if not pr_url_match:
                raise RuntimeError(f"Could not extract PR URL from output: {output}")

            pr_url = pr_url_match.group(0)

            logger.info("Created PR: %s", pr_url)
            return {
                "pr_url": pr_url,
            }

        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to create PR: {e.stderr.decode() if e.stderr else str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
        except Exception as e:
            error_msg = f"Failed to create PR: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic."""
        branch_name = self._branch_name
        if not branch_name:
            raise ValueError("branch_name is required")

        pr_title = self._pr_title
        if not pr_title:
            raise ValueError("pr_title is required")

        pr_body = self._pr_body or ""

        # Get repo_path (required for gh CLI)
        repo_path_str = self._repo_path
        if not repo_path_str:
            raise ValueError("repo_path is required")
        
        repo_path = Path(repo_path_str)
        if not repo_path.exists():
            raise ValueError(f"Repository path does not exist: {repo_path}")

        # Commit and push changes to the branch
        logger.info("Committing and pushing changes to branch %s", branch_name)
        commit_and_push_changes(str(repo_path), pr_title, new_branch=branch_name, allow_empty=False)

        # Create the PR using gh CLI
        pr_result = self._create_github_pr(
            repo_path=repo_path,
            branch_name=branch_name,
            pr_title=pr_title,
            pr_body=pr_body,
        )

        pr_url = pr_result["pr_url"]
        
        return {
            "pr_url": pr_url,
        }


if __name__ == "__main__":
    """Test the PRCreator directly."""
    import sys

    # Add project root to path
    from src.utils.setup.const import PROJECT_ROOT
    sys.path.insert(0, str(PROJECT_ROOT))

    from dotenv import load_dotenv

    # Load environment variables
    load_dotenv()

    # Create test state
    test_state: Dict[str, Any] = {
        "branch_name": "leozhu/CDP-3406-bot-20260203-142135",
        "pr_title": "Refactor Helm chart for cdp-event-ingestor",
        "pr_body": """## Summary

This PR refactors the Helm chart for `cdp-event-ingestor` to improve maintainability and environment-specific configuration. It introduces a reusable pod template, standardizes naming conventions with environment suffixes, and adds a staging configuration.

## Changes Made

- **Templates**:
    - Created `_podTemplate.yaml` to define a reusable `cdp-event-ingestor.podSpec` helper.
    - Updated `_helpers.tpl` to include `releaseNameWithEnv` and standardized `fullname` and `selectorLabels` to include environment context.
    - Refactored `deployment.yaml` to use the new `podSpec` helper and support dynamic `podLabels` and `podAnnotations`.
- **Configuration**:
    - Updated `values.yaml` with default resources, annotations, and environment variables.
    - Updated `values/development.yaml` and `values/production.yaml` to include environment metadata and deployment-specific settings (IAM roles, security groups).
    - Added `values/staging.yaml` for the staging environment.

## Verification

- Verified Helm template rendering for development, staging, and production environments.
- Confirmed that labels and annotations are correctly applied to the deployment and pods.""",
        "repo_path": str(PROJECT_ROOT / "go-segment"),
    }

    try:
        node = PRCreator()
        result = node.run(test_state)

        print("\n" + "=" * 80)
        print(" Execution Result:")
        print("=" * 80)
        print(f"PR URL: {result.get('pr_url')}")
        print("=" * 80)
    except Exception as e:
        print(f"Error running : {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
