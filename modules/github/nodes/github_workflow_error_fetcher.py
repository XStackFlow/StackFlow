"""GithubWorkflowErrorFetcher Node - Polls a GitHub Actions workflow run until completion and fetches error logs."""

import subprocess
import time
import json
from typing import Any, Dict, Optional
from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.log_utils import filter_error_logs
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class GithubWorkflowErrorFetcher(BaseNode):
    """
    Node that polls a GitHub Actions workflow run until completion,
    then returns the result and failed step logs.
    """

    def __init__(
        self,
        repo_name: Resolvable[str] = "",
        github_run_id: Resolvable[str] = "{{github_run_id}}",
        polling_interval: Resolvable[float] = 30.0,
        max_wait_seconds: Resolvable[float] = 600.0,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.repo_name = repo_name
        self.github_run_id = github_run_id
        self.polling_interval = polling_interval
        self.max_wait_seconds = max_wait_seconds

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = self._repo_name
        run_id = self._github_run_id

        if not repo_name:
            raise ValueError("repo_name is required.")
        if not run_id:
            raise ValueError("github_run_id is required.")

        logger.info(f"Monitoring GitHub Actions run {run_id} in {repo_name}")

        # Poll for completion
        start_time = time.time()
        conclusion = None
        polling_interval = self._polling_interval or 30.0
        max_wait_seconds = self._max_wait_seconds or 600.0

        while time.time() - start_time < max_wait_seconds:
            run_info = self._get_run_info(repo_name, run_id)
            status = run_info.get("status")
            conclusion = run_info.get("conclusion")

            if status == "completed":
                break

            logger.info(f"Run {run_id} is {status}. Waiting {polling_interval}s...")
            time.sleep(polling_interval)
        else:
            raise TimeoutError(f"Workflow run {run_id} timed out after {max_wait_seconds}s")

        # Fetch job and step information
        jobs = []
        cmd = ["gh", "run", "view", str(run_id), "--repo", repo_name, "--json", "jobs"]
        jobs_res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if jobs_res.returncode == 0:
            jobs = json.loads(jobs_res.stdout).get("jobs", [])
        else:
            logger.warning(f"Failed to fetch jobs for run {run_id}: {jobs_res.stderr.strip()}")

        failed_steps = []
        step_logs = {}

        for job in jobs:
            job_name = job.get("name", "")
            job_db_id = job.get("databaseId")

            for step in job.get("steps", []):
                if step.get("conclusion") == "failure":
                    step_name = step.get("name")
                    failed_steps.append(f"{job_name}: {step_name}")
                    logs = self._get_job_logs(repo_name, job_db_id)
                    if logs:
                        step_logs[f"{job_name}: {step_name}"] = logs

        workflow_result = {
            "gh_conclusion": conclusion,
            "gh_run_id": run_id,
            "failed_steps": failed_steps,
            "gh_action_logs": step_logs,
        }

        return {
            "workflow_result": workflow_result,
        }

    def _get_run_info(self, repo_name: str, run_id: str) -> Dict[str, Any]:
        cmd = ["gh", "run", "view", str(run_id), "--repo", repo_name, "--json", "status,conclusion"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.error(f"Error getting run info for {run_id}: {result.stderr.strip()}")
            return {}
        return json.loads(result.stdout)

    def _get_job_logs(self, repo_name: str, job_id: int) -> Optional[str]:
        cmd = ["gh", "run", "view", "--job", str(job_id), "--repo", repo_name, "--log"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.error(f"Error fetching job logs for {job_id}: {result.stderr.strip()}")
            return None
        return filter_error_logs(result.stdout)
