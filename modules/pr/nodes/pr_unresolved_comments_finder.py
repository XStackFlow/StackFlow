"""PR Unresolved Comments Finder — checks open PRs for review status."""

import json
import subprocess
from typing import Any, Dict, List, Optional

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger
from modules.pr.const import BLACKLISTED_COMMENT_STRINGS

logger = get_logger(__name__)

# Fetches unresolved review threads + approval state in one call.
_PR_STATUS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100) {
        nodes {
          isResolved
          comments(first: 1) {
            nodes {
              author { login }
              body
              path
              originalLine
            }
          }
        }
      }
      reviews(first: 50) {
        nodes {
          state
        }
      }
    }
  }
}
"""


class PRUnresolvedCommentsFinder(BaseNode):
    """Checks open PRs for items needing attention.

    A PR is included in the output if it has any unresolved review threads
    OR if it has not yet received an approved review (no LGTM).

    Expects ``open_prs`` in state (produced by FetchAllPRs).

    Outputs:
        prs_needing_attention: List of dicts, each containing:
            - url, title, number: PR identifiers
            - has_unresolved_comments: bool
            - unresolved_count: int
            - comments: list of {author, body, path, line}
            - is_approved: bool
    """

    def __init__(
        self,
        open_prs: Resolvable[list] = "{{open_prs}}",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.open_prs = open_prs

    def _fetch_pr_status(self, owner: str, repo: str, pr_number: int) -> Dict[str, Any]:
        """Fetch unresolved threads and approval state for a single PR."""
        result = subprocess.run(
            [
                "gh", "api", "graphql",
                "-f", f"query={_PR_STATUS_QUERY}",
                "-f", f"owner={owner}",
                "-f", f"repo={repo}",
                "-F", f"pr={pr_number}",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning("Failed to fetch PR status for %s/%s#%d: %s", owner, repo, pr_number, result.stderr)
            return {"comments": [], "is_approved": False}

        data = json.loads(result.stdout)
        pr_data = data["data"]["repository"]["pullRequest"]
        threads = pr_data["reviewThreads"]["nodes"]
        reviews = pr_data["reviews"]["nodes"]

        # Collect unresolved thread initiators (one entry per unresolved thread)
        comments = []
        for thread in threads:
            if thread["isResolved"]:
                continue
            thread_comments = thread["comments"]["nodes"]
            if not thread_comments:
                continue
            first = thread_comments[0]
            body = first.get("body", "")
            if any(b in body for b in BLACKLISTED_COMMENT_STRINGS):
                continue
            comments.append({
                "author": first.get("author", {}).get("login", ""),
                "body": body,
                "path": first.get("path"),
                "line": first.get("originalLine"),
            })

        is_approved = any(r.get("state") == "APPROVED" for r in reviews)

        return {"comments": comments, "is_approved": is_approved}

    def _parse_pr_url(self, url: str):
        """Extract owner, repo, pr_number from a GitHub PR URL."""
        parts = url.replace("https://github.com/", "").split("/")
        if len(parts) < 4:
            raise ValueError(f"Invalid PR URL: {url}")
        return parts[0], parts[1], int(parts[3])

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        open_prs = self._open_prs or []
        logger.info("PRUnresolvedCommentsFinder: Checking %d PRs", len(open_prs))

        prs_needing_attention = []
        for pr in open_prs:
            owner, repo, pr_number = self._parse_pr_url(pr["url"])
            status = self._fetch_pr_status(owner, repo, pr_number)

            has_unresolved = bool(status["comments"])
            is_approved = status["is_approved"]

            if has_unresolved or not is_approved:
                prs_needing_attention.append({
                    "url": pr["url"],
                    "title": pr["title"],
                    "number": pr["number"],
                    "has_unresolved_comments": has_unresolved,
                    "unresolved_count": len(status["comments"]),
                    "comments": status["comments"],
                    "is_approved": is_approved,
                })
                reasons = []
                if has_unresolved:
                    reasons.append(f"{len(status['comments'])} unresolved thread(s)")
                if not is_approved:
                    reasons.append("not approved")
                logger.info("  PR #%d (%s): needs attention — %s", pr["number"], pr["title"], ", ".join(reasons))
            else:
                logger.info("  PR #%d (%s): all clear", pr["number"], pr["title"])

        return {"prs_needing_attention": prs_needing_attention}
