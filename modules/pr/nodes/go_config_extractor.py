"""GoConfigExtractor - Extracts Go config structs from a PR's changed files."""

import re
import subprocess
import json
import base64
from typing import Any, Dict, Optional

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class GoConfigExtractor(BaseNode):
    """
    Node that extracts Go config structs from a PR.
    Looks for files matching config/config.go (or any *config.go),
    then returns the current and base-branch versions for LLM context.
    """

    def __init__(self, pr_url: Resolvable[str] = "{{pr_url}}", **kwargs):
        super().__init__(**kwargs)
        self.pr_url = pr_url

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        pr_url = self._pr_url
        if not pr_url:
            raise ValueError("pr_url is required")

        logger.info(f"Extracting Go config from PR: {pr_url}")

        # 1. Find config.go in the PR's changed files
        try:
            result = subprocess.run(
                ["gh", "pr", "view", pr_url, "--json", "files"],
                capture_output=True, text=True, check=True
            )
            files = [f["path"] for f in json.loads(result.stdout).get("files", [])]
        except Exception as e:
            raise RuntimeError(f"Failed to fetch PR files: {e}")

        config_file = next((f for f in files if f.endswith("config/config.go")), None)
        if not config_file:
            config_file = next((f for f in files if "config.go" in f), None)
        if not config_file:
            raise ValueError(f"No config.go found in PR {pr_url}")

        logger.info(f"Found config file: {config_file}")

        # 2. Fetch head and base content
        result = subprocess.run(
            ["gh", "pr", "view", pr_url, "--json", "headRefName,baseRefName,headRepositoryOwner,headRepository"],
            capture_output=True, text=True, check=True
        )
        pr_info = json.loads(result.stdout)
        owner = pr_info["headRepositoryOwner"]["login"]
        repo_name = pr_info["headRepository"]["name"]
        head_branch = pr_info["headRefName"]
        base_branch = pr_info["baseRefName"]

        def fetch_content(ref: str) -> str:
            api_url = f"repos/{owner}/{repo_name}/contents/{config_file}?ref={ref}"
            r = subprocess.run(["gh", "api", api_url, "--jq", ".content"], capture_output=True, text=True, check=True)
            return base64.b64decode(r.stdout.replace("\n", "").strip()).decode("utf-8")

        content = fetch_content(head_branch)
        previous_content = fetch_content(base_branch)

        combined = (
            f"--- CURRENT CONFIG (PR BRANCH) ---\n{content}\n\n"
            f"--- PREVIOUS CONFIG (BASE BRANCH) ---\n{previous_content}"
        )

        return {
            "config_struct": combined,
        }

    def _extract_config_structs(self, content: str) -> str:
        """Extract type Config struct and its dependencies."""
        pattern = r'type\s+Config\s+struct\s+\{(?:[^{}]|\{[^{}]*\})*\}'
        match = re.search(pattern, content)
        if not match:
            return ""
        extracted = [match.group(0)]
        config_body = match.group(0)
        potential_types = (
            re.findall(r'\s+([A-Z]\w+)\s+', config_body) +
            re.findall(r'\*([A-Z]\w+)', config_body) +
            re.findall(r'\[\]([A-Z]\w+)', config_body)
        )
        for t in sorted(set(potential_types)):
            if t == "Config":
                continue
            m = re.search(rf'type\s+{t}\s+struct\s+\{{(?:[^{{}}]|\{{[^{{}}]*\}})*\}}', content)
            if m:
                extracted.append(m.group(0))
        return "\n\n".join(extracted)
