"""GitHub RunGithubAction Node - Executes a GitHub Actions workflow using the gh CLI."""

import subprocess
from typing import Any, Dict
from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger
from src.inputs.standard_inputs import JSONString

logger = get_logger(__name__)


class RunGithubAction(BaseNode):
    """Node that executes a GitHub Actions workflow with specified fields."""

    def __init__(
        self,
        workflow: Resolvable[str] = "",
        branch: Resolvable[str] = "{{branch}}",
        additional_fields: Resolvable[JSONString] = {},
        repo_path: Resolvable[str] = "{{repo_path}}",
        **kwargs
    ):
        """Initialize the RunGithubAction node.

        Args:
            workflow: The workflow filename or ID (e.g., 'deploy.yml').
            branch: The branch to run the workflow on (template supported).
            additional_fields: Dictionary of additional fields to pass to the workflow (template supported).
            repo_path: The local repository path to execute the command in (template supported).
            **kwargs: Additional properties from LiteGraph.
        """
        super().__init__(**kwargs)
        self.workflow = workflow
        self.branch = branch
        self.additional_fields = additional_fields
        self.repo_path = repo_path

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the GitHub CLI command to trigger the workflow.

        Args:
            state: Node state containing variables for template rendering.

        Returns:
            Minimal state update with the github_run_id.
        """
        # 1. Access Resolved Attributes
        workflow = self._workflow
        branch = self._branch
        additional_fields = self._additional_fields or {}
        if isinstance(additional_fields, str):
            import json
            additional_fields = json.loads(additional_fields) if additional_fields.strip() else {}
        repo_path = self._repo_path

        # 2. Validate Inputs
        if not workflow:
            raise ValueError("workflow is required (e.g. 'deploy.yml').")

        if not branch:
            raise ValueError("branch is required.")

        if not repo_path:
            raise ValueError("repo_path is required to run GitHub CLI commands in the correct repository context.")

        # 3. Construct Command
        cmd = [
            "gh", "workflow", "run", workflow,
            "--ref", branch
        ]
        
        # Add dynamic fields
        for key, value in additional_fields.items():
            cmd.extend(["--field", f"{key}={value}"])

        logger.info("Triggering GitHub Action in %s: %s", repo_path, " ".join(cmd))

        from datetime import datetime, timezone, timedelta
        trigger_time = datetime.now(timezone.utc) - timedelta(seconds=5)  # Buffer for clock skew

        # 4. Execute Command
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode != 0:
            error_msg = f"Failed to trigger GitHub Action: {result.stderr.strip()}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        
        logger.info("Workflow triggered successfully. Fetching run ID...")
        
        # 5. Fetch Run ID (poll until we find the new run)
        import time
        import json
        
        run_id = None
        
        logger.info(f"Polling for new run on branch '{branch}' (Triggered at {trigger_time})...")
        
        for attempt in range(5):
            time.sleep(3) # Wait between polls
            
            list_cmd = [
                "gh", "run", "list", 
                "--workflow", workflow, 
                "--branch", branch, 
                "--limit", "5", # Get a few to be safe
                "--json", "databaseId,createdAt"
            ]
            list_result = subprocess.run(
                list_cmd, 
                cwd=repo_path, 
                capture_output=True, 
                text=True, 
                check=False
            )
            
            if list_result.returncode != 0:
                logger.warning(f"Attempt {attempt + 1} failed to list runs: {list_result.stderr.strip()}")
                continue
                
            runs = json.loads(list_result.stdout)
            
            for run in runs:
                # Parse GH timestamp: "2026-02-24T21:17:41Z"
                created_at = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
                
                # If this run was created after we triggered, it's our run!
                if created_at >= trigger_time:
                    run_id = str(run["databaseId"])
                    break
            
            if run_id:
                break
                
            logger.info(f"Attempt {attempt + 1}: New run not found yet. Redrying...")
        
        if not run_id:
            logger.warning("Could not definitively identify the run ID.")
        else:
            logger.info(f"Identified Run ID: {run_id}")

        return {
            "github_run_id": run_id,
        }
