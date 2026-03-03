"""PR Feedback Getter — fetches PR feedback (reviews, comments, check runs) and failed logs."""

import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger
from src.utils.log_utils import filter_error_logs

logger = get_logger(__name__)

_REVIEW_THREADS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100) {
        nodes {
          isResolved
          comments(first: 50) {
            nodes {
              databaseId
              author { login }
              body
              createdAt
              path
              originalLine
            }
          }
        }
      }
    }
  }
}
"""

_ISSUE_COMMENTS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      comments(first: 100) {
        nodes {
          databaseId
          author { login }
          body
          createdAt
          isMinimized
        }
      }
    }
  }
}
"""


class PRFeedbackGetter(BaseNode):
    """Node that fetches PR feedback (reviews, comments, check runs) and failed logs using gh CLI."""

    def __init__(self, pr_url: Resolvable[str] = "{{pr_url}}", **kwargs):
        super().__init__(**kwargs)
        self.pr_url = pr_url
        whitelisted_authors_env = os.getenv("WHITELISTED_COMMENT_AUTHORS", "")
        self._whitelisted_authors = set(a.strip() for a in whitelisted_authors_env.split(",") if a.strip())

        from modules.pr.const import BLACKLISTED_COMMENT_STRINGS, BLACKLISTED_CHECK_NAMES
        self._blacklisted_strings = BLACKLISTED_COMMENT_STRINGS
        self._blacklisted_check_names = BLACKLISTED_CHECK_NAMES

    def _get_job_id(self, details_url: str) -> Optional[str]:
        match = re.search(r"/job/(\d+)", details_url)
        return match.group(1) if match else None

    def _fetch_logs(self, job_id: str, repo: str) -> str:
        result = subprocess.run(
            ["gh", "run", "view", "--job", job_id, "--log", "--repo", repo],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            if "is still in progress" in result.stderr:
                logger.info("Job %s is still in progress, skipping log fetch", job_id)
                return ""
            raise RuntimeError(f"Failed to fetch logs for job {job_id}: {result.stderr}")
        return filter_error_logs(result.stdout)

    def _fetch_issue_comments(self, owner: str, repo: str, pr_number: str) -> List[Dict[str, Any]]:
        """Fetch non-minimized top-level PR (issue) comments via GraphQL.

        Uses GraphQL instead of the REST API so we can filter out minimized
        comments (GitHub's mechanism for marking comments as resolved/off-topic).
        """
        result = subprocess.run(
            [
                "gh", "api", "graphql",
                "-f", f"query={_ISSUE_COMMENTS_QUERY}",
                "-f", f"owner={owner}",
                "-f", f"repo={repo}",
                "-F", f"pr={int(pr_number)}",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to fetch issue comments: {result.stderr}")

        nodes = json.loads(result.stdout)["data"]["repository"]["pullRequest"]["comments"]["nodes"]
        comments = []
        for c in nodes:
            if c.get("isMinimized"):
                continue
            comments.append({
                "id": c["databaseId"],
                "author": c["author"],
                "body": c["body"],
                "createdAt": c["createdAt"],
            })
        return comments

    def _fetch_unresolved_review_comments(self, owner: str, repo: str, pr_number: str) -> List[Dict[str, Any]]:
        """Fetch only unresolved inline review thread comments via GraphQL."""
        result = subprocess.run(
            [
                "gh", "api", "graphql",
                "-f", f"query={_REVIEW_THREADS_QUERY}",
                "-f", f"owner={owner}",
                "-f", f"repo={repo}",
                "-F", f"pr={int(pr_number)}",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to fetch review threads: {result.stderr}")

        threads = json.loads(result.stdout)["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        comments = []
        for thread in threads:
            if thread["isResolved"]:
                continue
            for c in thread["comments"]["nodes"]:
                comments.append({
                    "id": c["databaseId"],
                    "author": c["author"],
                    "body": c["body"],
                    "createdAt": c["createdAt"],
                    "path": c["path"],
                    "line": c["originalLine"],
                })
        return comments

    def _fetch_feedback(self, pr_url: str) -> Dict[str, Any]:
        try:
            url_parts = pr_url.replace("https://github.com/", "").split("/")
            if len(url_parts) < 4:
                raise ValueError(f"Invalid PR URL: {pr_url}")
            owner = url_parts[0]
            repo_short = url_parts[1]
            repo = f"{owner}/{repo_short}"
            pr_number = url_parts[3]

            # 1. Basic PR info (retry if mergeable is UNKNOWN — GitHub computes it async)
            fields = ["statusCheckRollup", "state", "reviewDecision", "headRefName", "mergeable"]
            feedback = None
            for attempt in range(3):
                result = subprocess.run(
                    ["gh", "pr", "view", pr_url, "--json", ",".join(fields)],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"Failed to fetch PR info: {result.stderr}")
                feedback = json.loads(result.stdout)
                if feedback.get("mergeable") != "UNKNOWN":
                    break
                logger.info("mergeable is UNKNOWN, retrying in 3s (attempt %d/3)...", attempt + 1)
                time.sleep(3)

            # 2. Top-level issue comments (GraphQL — excludes minimized/hidden comments)
            issue_comments = self._fetch_issue_comments(owner, repo_short, pr_number)

            # 3. Unresolved inline review thread comments (GraphQL — already excludes resolved threads)
            review_comments = self._fetch_unresolved_review_comments(owner, repo_short, pr_number)

            feedback["comments"] = issue_comments + review_comments
            return self._filter_feedback(feedback)

        except Exception as e:
            logger.error("Error fetching PR feedback: %s", e)
            raise RuntimeError(f"Failed to fetch PR feedback: {str(e)}")

    def _filter_feedback(self, feedback: Dict[str, Any]) -> Dict[str, Any]:
        """Apply author whitelist, blacklist strings, and check status filters."""
        if "comments" in feedback:
            feedback["comments"] = [
                c for c in feedback["comments"]
                if c.get("author", {}).get("login", "") in self._whitelisted_authors
                and not any(b in c.get("body", "") for b in self._blacklisted_strings)
            ]

        if "statusCheckRollup" in feedback:
            status_rollup = feedback.get("statusCheckRollup")
            if isinstance(status_rollup, list):
                feedback["statusCheckRollup"] = [
                    c for c in status_rollup
                    if self._get_check_state(c) not in ["SUCCESS", "SKIPPED", "PENDING"]
                    and c.get("name") not in self._blacklisted_check_names
                ]

        return feedback

    def _get_check_state(self, check: dict) -> str:
        return check.get("conclusion") if "conclusion" in check else check.get("state")

    def _enrich_feedback(self, feedback: Dict[str, Any], pr_url: str) -> Dict[str, Any]:
        """Embed logs for failed checks."""
        checks = feedback.get("statusCheckRollup", [])
        if not checks or any(c.get("status") == "IN_PROGRESS" for c in checks):
            return feedback

        url_parts = pr_url.replace("https://github.com/", "").split("/")
        repo = f"{url_parts[0]}/{url_parts[1]}"

        for check in checks:
            if self._get_check_state(check) == "FAILURE":
                job_id = self._get_job_id(check.get("detailsUrl", ""))
                if job_id:
                    logger.info("Fetching logs for failed check: %s (job %s)", check.get("name"), job_id)
                    check["logs"] = self._fetch_logs(job_id, repo)[-10000:]
                else:
                    logger.warning("Could not find job ID for failed check: %s", check.get("name"))
        return feedback

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        pr_url = self._pr_url
        if not pr_url:
            raise ValueError("pr_url is required")

        url_parts = pr_url.replace("https://github.com/", "").split("/")
        if len(url_parts) < 2:
            raise ValueError(f"Could not extract repo_name from pr_url: {pr_url}")
        repo_name = f"{url_parts[0]}/{url_parts[1]}"

        logger.info("Fetching feedback for PR: %s (%s)", pr_url, repo_name)
        feedback = self._fetch_feedback(pr_url)
        feedback = self._enrich_feedback(feedback, pr_url)

        return {
            "pr_feedback": feedback,
            "repo_name": repo_name,
        }


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    pr_url = "https://github.com/your-org/your-repo/pull/1"
    node = PRFeedbackGetter()
    result = node.run({"pr_url": pr_url})
    print(json.dumps(result["pr_feedback"], indent=2))
