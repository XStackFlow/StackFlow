"""PR Categorizer — categorises open PRs by their review state."""

import json
import subprocess
from typing import Any, Dict, List

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
          body
          author { login }
        }
      }
      comments(first: 100) {
        nodes {
          body
          author { login }
        }
      }
    }
  }
}
"""


class PRCategorizer(BaseNode):
    """Categorises open PRs into three buckets by review state.

    Buckets:
        - prs_ready_to_merge: approved (LGTM) AND no unresolved threads
        - prs_pending_change: has unresolved review threads (needs attention)
        - prs_pending_review: I haven't approved AND no unresolved threads

    Expects ``open_prs`` in state (produced by FetchAllPRs).

    Outputs:
        prs_ready_to_merge:  list of {url, title, number}
        prs_pending_change:  list of {url, title, number, unresolved_count, comments}
        prs_pending_review:  list of {url, title, number}
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
            logger.warning(
                "Failed to fetch PR status for %s/%s#%d: %s",
                owner, repo, pr_number, result.stderr,
            )
            return {"comments": [], "is_approved": False}

        data = json.loads(result.stdout)
        pr_data = data["data"]["repository"]["pullRequest"]
        threads = pr_data["reviewThreads"]["nodes"]
        reviews = pr_data["reviews"]["nodes"]
        pr_comments = pr_data.get("comments", {}).get("nodes", [])

        # Collect unresolved thread initiators (one entry per unresolved thread)
        unresolved = []
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
            unresolved.append({
                "author": first.get("author", {}).get("login", ""),
                "body": body,
                "path": first.get("path"),
                "line": first.get("originalLine"),
            })

        is_approved = any(r.get("state") == "APPROVED" for r in reviews)

        # Count formal approvals + "LGTM" in review bodies + "LGTM" in PR comments
        approved_by = set()
        for r in reviews:
            login = r.get("author", {}).get("login", "")
            if not login:
                continue
            if r.get("state") == "APPROVED":
                approved_by.add(login)
            elif "lgtm" in (r.get("body") or "").lower():
                approved_by.add(login)

        for c in pr_comments:
            login = c.get("author", {}).get("login", "")
            if not login:
                continue
            if "lgtm" in (c.get("body") or "").lower():
                approved_by.add(login)

        return {"comments": unresolved, "is_approved": is_approved, "approved_by": approved_by}

    def _parse_pr_url(self, url: str):
        """Extract owner, repo, pr_number from a GitHub PR URL."""
        parts = url.replace("https://github.com/", "").split("/")
        if len(parts) < 4:
            raise ValueError(f"Invalid PR URL: {url}")
        return parts[0], parts[1], int(parts[3])

    def _get_github_username(self) -> str:
        """Get the authenticated GitHub username."""
        result = subprocess.run(
            ["gh", "api", "user", "-q", ".login"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise ValueError(f"Failed to get GitHub username: {result.stderr}")
        return result.stdout.strip()

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        open_prs = self._open_prs or []
        my_login = self._get_github_username()
        logger.info("PRCategorizer: Checking %d PRs (my login: %s)", len(open_prs), my_login)

        prs_ready_to_merge: List[Dict] = []
        prs_pending_change: List[Dict] = []
        prs_pending_review: List[Dict] = []

        for pr in open_prs:
            owner, repo, pr_number = self._parse_pr_url(pr["url"])
            status = self._fetch_pr_status(owner, repo, pr_number)

            has_unresolved = bool(status["comments"])
            is_approved = status["is_approved"]
            i_approved = my_login in status["approved_by"]

            base = {
                "url": pr["url"],
                "title": pr["title"],
                "number": pr["number"],
                "repo_name": f"{owner}/{repo}",
            }

            if has_unresolved:
                # Needs changes regardless of approval state
                prs_pending_change.append({
                    **base,
                    "unresolved_count": len(status["comments"]),
                    "comments": status["comments"],
                })
                logger.info(
                    "  PR #%d (%s): pending change — %d unresolved thread(s)",
                    pr["number"], pr["title"], len(status["comments"]),
                )
            elif is_approved:
                prs_ready_to_merge.append(base)
                logger.info("  PR #%d (%s): ready to merge", pr["number"], pr["title"])
            elif not i_approved:
                prs_pending_review.append(base)
                logger.info("  PR #%d (%s): pending review (no LGTM from me)", pr["number"], pr["title"])

        return {
            "prs_ready_to_merge": prs_ready_to_merge,
            "prs_pending_change": prs_pending_change,
            "prs_pending_review": prs_pending_review,
        }
