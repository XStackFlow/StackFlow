"""Jira Ticket Preparer Node - Creates Jira tickets if they don't exist."""

import json
import re
import subprocess
from typing import Any, Dict, List

from src.nodes.abstract.base_node import BaseNode
from src.inputs.standard_inputs import Resolvable
from src.utils.setup.logger import get_logger
from modules.jira.inputs import IssueType

logger = get_logger(__name__)


class JiraTicketPreparer(BaseNode):
    """Node that prepares Jira tickets by creating them if they don't already exist."""

    def __init__(
        self,
        project_key: Resolvable[str] = "{{project_key}}",
        keyword_templates: Resolvable[str] = "",
        summary_template: Resolvable[str] = None,
        issue_type: IssueType = "Task",
        **kwargs
    ):
        """Initialize the Jira ticket preparer node.

        Args:
            project_key: The Jira project key (e.g. "CDP").
            keyword_templates: Comma-separated keyword templates (e.g. "{{service}}, staging")
            summary_template: Ticket summary/title template (supports {{variable}}).
            issue_type: Type of issue to create (default: "Task").
            **kwargs: Additional properties from LiteGraph.
        """
        super().__init__(**kwargs)
        self.project_key = project_key
        self.keyword_templates = keyword_templates
        self.summary_template = summary_template
        self.issue_type = issue_type

    def _search_tickets(self) -> List[Dict[str, Any]]:
        """Search for existing Jira tickets using keywords.

        Returns:
            List of ticket dictionaries with keys like 'key', 'summary', 'status'
        """
        # Build JQL query: search for tickets in project with keywords in summary
        # Escape quotes in keywords to prevent JQL injection
        keywords = [k.strip() for k in self._keyword_templates.split(",") if k.strip()]
        escaped_keywords = [keyword.replace('"', '\\"') for keyword in keywords]
        # Build query: search for tickets containing ALL keywords in summary
        keyword_conditions = [f'summary ~ "{keyword}"' for keyword in escaped_keywords]
        keyword_query = " AND ".join(keyword_conditions)
        jql = f'project = {self._project_key} AND ({keyword_query})'
        
        logger.info("Executing JQL query: %s", jql)

        result = subprocess.run(
            ["jira", "issue", "list", "--jql", jql, "--raw"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Jira search failed: {result.stderr}")

        # Parse JSON output from --raw flag
        if result.stdout.strip():
            try:
                tickets_data = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Failed to parse Jira search results: {e}") from e
            # Handle both array and object formats
            if isinstance(tickets_data, list):
                tickets = tickets_data
            elif isinstance(tickets_data, dict) and "issues" in tickets_data:
                tickets = tickets_data["issues"]
            else:
                tickets = []
        else:
            tickets = []
        return tickets

    def _create_ticket(self, summary: str) -> str:
        """Create a Jira ticket.

        Args:
            summary: The rendered summary for the ticket.

        Returns:
            Ticket key (e.g., "PROJ-123") if created successfully
        """
        # Create ticket using Jira CLI
        summary_with_bot = summary
        cmd = [
            "jira",
            "issue",
            "create",
            "--project",
            self._project_key,
            "--type",
            self.issue_type,
            "--summary",
            summary_with_bot,
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to create Jira ticket: {result.stderr}")

        # Extract ticket key from output (usually in format "Created PROJ-123")
        output = result.stdout.strip()
        # Try to find ticket key pattern (e.g., "PROJ-123")
        match = re.search(rf"{self._project_key}-\d+", output)
        if match:
            ticket_key = match.group(0)
            logger.info("Created Jira ticket: %s", ticket_key)
            return ticket_key
        else:
            raise RuntimeError(f"Could not extract ticket key from output: {output}")

    def _run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the node logic.

        Args:
            state: Node state

        Returns:
            Updated state with ticket_key
        """
        # 1. Validation
        if not self._project_key:
            raise ValueError("project_key is required.")
        
        if not self._keyword_templates:
             raise ValueError("keyword_templates is required.")
        
        if not self._summary_template:
            raise ValueError("summary_template is required.")

        try:
            # 2. Search for existing tickets
            existing_tickets = self._search_tickets()
            
            if existing_tickets:
                ticket_key = existing_tickets[0].get("key")
                if ticket_key:
                    logger.info("Found existing ticket: %s", ticket_key)
                    return {
                        "ticket_key": ticket_key,
                    }
                else:
                    raise RuntimeError(f"Found existing tickets but no key field in response: {existing_tickets[0]}")

            # 3. No existing ticket found, create a new one
            ticket_key = self._create_ticket(self._summary_template)

            return {
                "ticket_key": ticket_key,
            }
        except Exception as e:
            logger.error("JiraTicketPreparer failed: %s", e)
            raise


if __name__ == "__main__":
    """Test the JiraTicketPreparer directly."""
    import sys
    import os
    from pathlib import Path
    
    # Add project root to path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
    
    from dotenv import load_dotenv
    
    # Load environment variables
    load_dotenv()
    
    # Create test state
    service_name = "cdp-event-ingestor"
    test_state: Dict[str, Any] = {}
    
    try:
        node = JiraTicketPreparer(
            project_key="CDP",
            keywords=[service_name, "staging"],
            summary=f"Onboard {service_name} to staging",
            issue_type="Task",
        )
        result = node.run(test_state)
        
        print("\n" + "=" * 80)
        print("Node Execution Result:")
        print("=" * 80)
        print(f"Ticket key: {result.get('ticket_key')}")
        print("=" * 80)
    except Exception as e:
        print(f"Error running node: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
