"""PR Comment Resolver — resolves all review thread comments from pr_feedback via GitHub GraphQL."""

import json
import subprocess
from typing import Any, Dict, Set

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)

_RESOLVE_THREADS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          comments(first: 1) {
            nodes { databaseId }
          }
        }
      }
    }
  }
}
"""

_RESOLVE_MUTATION = """
mutation($id: ID!) {
  resolveReviewThread(input: {threadId: $id}) {
    thread { isResolved }
  }
}
"""


class PRCommentResolver(BaseNode):
    """Marks all inline review thread comments in pr_feedback as resolved via GitHub GraphQL."""

    def __init__(
        self,
        pr_url: Resolvable[str] = "{{pr_url}}",
        pr_feedback: Resolvable[Dict[str, Any]] = "{{pr_feedback}}",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.pr_url = pr_url
        self.pr_feedback = pr_feedback

    def _parse_pr_url(self, pr_url: str):
        parts = pr_url.replace("https://github.com/", "").split("/")
        if len(parts) < 4:
            raise ValueError(f"Invalid PR URL: {pr_url}")
        return parts[0], parts[1], int(parts[3])  # owner, repo, pr_number

    def _fetch_thread_ids(self, owner: str, repo: str, pr_number: int, comment_ids: Set[int]) -> list:
        """Return GraphQL thread IDs for unresolved threads whose first comment is in comment_ids."""
        result = subprocess.run(
            [
                "gh", "api", "graphql",
                "-f", f"query={_RESOLVE_THREADS_QUERY}",
                "-f", f"owner={owner}",
                "-f", f"repo={repo}",
                "-F", f"pr={pr_number}",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to fetch review threads: {result.stderr}")

        threads = json.loads(result.stdout)["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        return [
            t["id"] for t in threads
            if not t["isResolved"]
            and t["comments"]["nodes"]
            and t["comments"]["nodes"][0]["databaseId"] in comment_ids
        ]

    def _resolve_thread(self, thread_id: str) -> None:
        result = subprocess.run(
            [
                "gh", "api", "graphql",
                "-f", f"query={_RESOLVE_MUTATION}",
                "-f", f"id={thread_id}",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning("Failed to resolve thread %s: %s", thread_id, result.stderr)
        else:
            logger.info("Resolved review thread %s", thread_id)

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        pr_url = self._pr_url
        if not pr_url:
            raise ValueError("pr_url is required")

        pr_feedback = self._pr_feedback or {}
        comments = pr_feedback.get("comments", [])

        # Only inline review comments (not top-level issue comments) belong to threads.
        # Review comments have a 'path' field; issue comments do not.
        comment_ids = {c["id"] for c in comments if c.get("id") and c.get("path")}

        if not comment_ids:
            logger.info("No review thread comments to resolve")
            return {}

        owner, repo, pr_number = self._parse_pr_url(pr_url)
        thread_ids = self._fetch_thread_ids(owner, repo, pr_number, comment_ids)

        logger.info("Resolving %d review thread(s)", len(thread_ids))
        for thread_id in thread_ids:
            self._resolve_thread(thread_id)

        return {}
