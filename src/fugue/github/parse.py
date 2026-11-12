"""Pure conversion of GitHub JSON responses into Fugue models."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from fugue.models import Ticket


def parse_body_sections(body: str) -> dict[str, str]:
    """Keep Markdown level-two sections, including their original contents."""
    parts = re.split(r"^##[ \t]+(.+?)[ \t]*$", body, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def project_items(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract project items from the issue GraphQL response."""
    return list(response["data"]["repository"]["issue"]["projectItems"]["nodes"])


def parse_ticket(
    issue: Mapping[str, Any],
    field_values: Sequence[Mapping[str, Any]],
    project_item: Mapping[str, Any],
) -> Ticket:
    """Combine the REST issue, native issue fields and selected project item."""
    fields = {
        entry["issue_field_name"]: (entry.get("single_select_option") or {}).get(
            "name", entry.get("value")
        )
        for entry in field_values
    }
    components = fields.get("Component") or []
    if isinstance(components, str):
        components = [components]
    components = [item["name"] if isinstance(item, dict) else item for item in components]
    parent_url = issue.get("parent_issue_url")
    return Ticket.model_validate(
        {
            "number": issue["number"],
            "title": issue["title"],
            "issue_type": issue["type"]["name"],
            "size": project_item["size"]["name"],
            "status": project_item["status"]["name"],
            "priority": fields["Priority"],
            "milestone": (issue.get("milestone") or {}).get("title"),
            "assignees": [entry["login"] for entry in issue.get("assignees", [])],
            "parent": int(parent_url.rstrip("/").rsplit("/", 1)[1]) if parent_url else None,
            "blocked_by": [entry["number"] for entry in issue.get("blocked_by", [])],
            "components": components,
            "body_sections": parse_body_sections(issue.get("body") or ""),
        }
    )
