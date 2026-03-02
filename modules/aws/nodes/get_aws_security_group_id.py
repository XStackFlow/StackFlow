"""Node to get AWS Security Group ID by name."""

import subprocess
import json
from typing import Any, Dict
from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger

logger = get_logger(__name__)


class GetAWSSecurityGroupID(BaseNode):
    """Node that retrieves an AWS Security Group ID given its name."""

    def __init__(self, security_group_name: Resolvable[str] = "", region: Resolvable[str] = "us-east-1", output_key: Resolvable[str] = "security_group_id", **kwargs):
        """Initialize the node.
        
        Args:
            security_group_name: The name of the security group.
            region: The AWS region to query. Defaults to us-east-1.
            output_key: The key to store the resultant security group ID in the state.
            **kwargs: Additional keyword arguments for the base class.
        """
        super().__init__(**kwargs)
        self.security_group_name = security_group_name
        self.region = region
        self.output_key = output_key

    async def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic (calls aws cli)."""
        sg_name = self._security_group_name
        region = self._region or "us-east-1"
        
        if not sg_name:
            raise ValueError("GetAWSSecurityGroupID: No security_group_name provided.")

        logger.info(f"GetAWSSecurityGroupID: Fetching ID for security group '{sg_name}' in region '{region}'...")

        cmd = [
            "aws", "ec2", "describe-security-groups",
            "--region", region,
            "--filters", f"Name=group-name,Values={sg_name}",
            "--query", "SecurityGroups[0].GroupId",
            "--output", "text"
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            connectivity_keywords = (
                "ExpiredToken", "InvalidClientTokenId", "AuthFailure",
                "NoCredentialsError", "Unable to locate credentials",
                "Error loading SSO", "Token has expired", "not authorized",
                "Could not connect", "Unable to connect", "ConnectionError",
                "endpoint URL", "UnauthorizedException",
            )
            hint = ""
            if any(kw.lower() in stderr.lower() for kw in connectivity_keywords):
                hint = " — try running: aws sso login"
            error_msg = f"AWS CLI Error: {stderr}{hint}"
            logger.error(f"GetAWSSecurityGroupID: {error_msg}")
            raise ValueError(error_msg)

        sg_id = result.stdout.strip()
        if not sg_id or sg_id == "None":
            error_msg = f"Security group '{sg_name}' not found in region '{region}'."
            logger.error(f"GetAWSSecurityGroupID: {error_msg}")
            raise ValueError(error_msg)

        logger.info(f"GetAWSSecurityGroupID: Found ID {sg_id} for group '{sg_name}'.")
        
        return {self._output_key: sg_id}
