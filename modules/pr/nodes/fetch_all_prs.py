"""FetchAllPRs — lists all open PRs authored by the current user."""

import json
import subprocess
from typing import Any, Dict, List

from src.nodes.abstract.base_node import BaseNode
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class FetchAllPRs(BaseNode):
    """Fetches all open pull requests authored by the current GitHub user.

    Uses the authenticated ``gh`` CLI. Output can be piped into nodes that
    operate on a list of PRs (e.g. PRUnresolvedCommentsFinder).

    Outputs:
        open_prs: List of dicts with ``url``, ``title``, ``number``.
    """

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        result = subprocess.run(
            [
                "gh", "search", "prs",
                "--author", "@me",
                "--state", "open",
                "--json", "url,title,number",
                "--limit", "100",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to list open PRs: {result.stderr}")

        open_prs: List[Dict[str, Any]] = json.loads(result.stdout)
        logger.info("FetchAllPRs: Found %d open PRs", len(open_prs))

        return {"open_prs": open_prs}
