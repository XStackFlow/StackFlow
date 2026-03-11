"""RepoFileFetcher - Fetches a file from a GitHub repo and stores it in state."""

import subprocess
import base64
from typing import Any, Dict

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class RepoFileFetcher(BaseNode):
    """
    Node that fetches a file from a GitHub repository via the API
    and stores its content in state under ``fetched_file_content``.

    Properties:
        repo:      GitHub repo in owner/name format (e.g. "StackAdapt/access").
        file_path: Path within the repo. Supports {{state.var}} interpolation.
        ref:       Git ref (branch/tag/sha) to fetch from. Default: "main".
        optional:  If true, missing files produce a warning instead of an error.
    """

    def __init__(
        self,
        repo: Resolvable[str] = "",
        file_path: Resolvable[str] = "",
        ref: Resolvable[str] = "main",
        optional: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.repo = repo
        self.file_path = file_path
        self.ref = ref
        self.optional = optional

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        repo = self._repo
        file_path = self._file_path
        ref = self._ref

        if not repo or not file_path:
            raise ValueError("repo and file_path are required")

        logger.info("Fetching %s from %s@%s", file_path, repo, ref)

        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/contents/{file_path}?ref={ref}", "--jq", ".content"],
            capture_output=True, text=True, check=False, timeout=30,
        )

        if result.returncode != 0:
            msg = f"Failed to fetch {file_path} from {repo}@{ref}: {result.stderr.strip()}"
            if self.optional:
                logger.warning("%s — continuing with empty value", msg)
                content = ""
            else:
                raise RuntimeError(msg)
        else:
            content_b64 = result.stdout.replace("\n", "").strip()
            if not content_b64 or content_b64 == "null":
                msg = f"GitHub API returned no content for {file_path} from {repo}@{ref} (file may be too large or binary)"
                if self.optional:
                    logger.warning("%s — continuing with empty value", msg)
                    content = ""
                else:
                    raise RuntimeError(msg)
            else:
                content = base64.b64decode(content_b64).decode("utf-8")
                logger.info("Fetched %d bytes from %s/%s", len(content), repo, file_path)

        return {"fetched_file_content": content}
