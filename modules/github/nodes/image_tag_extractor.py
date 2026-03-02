"""ImageTagExtractor Node - Extracts image tag from GitHub Actions workflow run logs."""

import subprocess
import json
import re
from typing import Any, Dict, Optional
from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class ImageTagExtractor(BaseNode):
    """
    Node that extracts the image tag from deploy/release/build job logs
    of a GitHub Actions workflow run.
    """

    def __init__(
        self,
        repo_name: Resolvable[str] = "",
        github_run_id: Resolvable[str] = "{{github_run_id}}",
        **kwargs
    ):
        super().__init__(**kwargs)
        self.repo_name = repo_name
        self.github_run_id = github_run_id

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        repo_name = self._repo_name
        run_id = self._github_run_id

        if not repo_name:
            raise ValueError("repo_name is required.")
        if not run_id:
            raise ValueError("github_run_id is required.")

        logger.info(f"Extracting image tag from run {run_id} in {repo_name}")

        # Fetch jobs for the run
        cmd = ["gh", "run", "view", str(run_id), "--repo", repo_name, "--json", "jobs"]
        jobs_res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if jobs_res.returncode != 0:
            logger.warning(f"Failed to fetch jobs for run {run_id}: {jobs_res.stderr.strip()}")
            return {"image_tag": None}

        jobs = json.loads(jobs_res.stdout).get("jobs", [])

        # Collect deploy/release/build job IDs
        deploy_job_ids = []
        for job in jobs:
            job_name = job.get("name", "")
            job_db_id = job.get("databaseId")
            job_conclusion = job.get("conclusion")

            if any(kw in job_name.lower() for kw in ["deploy", "release", "build"]):
                if job_conclusion != "skipped" and job_db_id:
                    deploy_job_ids.append(job_db_id)

        # Search logs for image tag
        image_tag = None
        for job_id in deploy_job_ids:
            if image_tag:
                break
            raw_cmd = ["gh", "run", "view", "--job", str(job_id), "--repo", repo_name, "--log"]
            raw_logs_res = subprocess.run(raw_cmd, capture_output=True, text=True, check=False)
            if raw_logs_res.returncode == 0:
                match = re.search(r'["\']imageTag["\']:\s*["\']([^"\']+)["\']', raw_logs_res.stdout)
                if not match:
                    match = re.search(r'imageTag=([a-zA-Z0-9.\-_]+)', raw_logs_res.stdout)
                if match:
                    image_tag = match.group(1)
                    logger.info(f"Extracted image tag: {image_tag}")

        return {"image_tag": image_tag}
