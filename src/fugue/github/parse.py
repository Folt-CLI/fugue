"""Pure conversion of GitHub JSON responses into Fugue models."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from fugue.models import Ticket


def flatten_pages(
    pages: Sequence[dict[str, Any] | Sequence[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Flatten slurped list pages, also accepting a single non-paged array."""
    items: list[dict[str, Any]] = []
    for page in pages:
        if isinstance(page, dict):
            items.append(page)
        else:
            items.extend(page)
    return items


def parse_body_sections(body: str) -> dict[str, str]:
    """Parse Markdown level-two sections with normalized newlines and headings."""
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    sections: dict[str, str] = {}
    heading: str | None = None
    contents: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    for line in body.splitlines(keepends=True):
        text = line.removesuffix("\n")
        if fence_character is not None:
            if re.fullmatch(
                r" {0,3}" + re.escape(fence_character) + r"{" + str(fence_length) + r",}[ \t]*",
                text,
            ):
                fence_character = None
        else:
            fence = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", text)
            if fence and (fence[1][0] != "`" or "`" not in fence[2]):
                fence_character = fence[1][0]
                fence_length = len(fence[1])
            else:
                match = re.fullmatch(r"##[ \t]+(.+?)[ \t]*", text)
                if match:
                    if heading is not None:
                        sections[heading] = "".join(contents).strip()
                    heading = match[1].strip()
                    contents = []
                    continue
        contents.append(line)
    if heading is not None:
        sections[heading] = "".join(contents).strip()
    return sections


def project_items(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract project items from the issue GraphQL response."""
    return list(response["data"]["repository"]["issue"]["projectItems"]["nodes"])


def parse_ticket(
    issue: Mapping[str, Any],
    field_values: Sequence[Mapping[str, Any]],
    project_item: Mapping[str, Any],
    blockers: Sequence[Mapping[str, Any]] = (),
) -> Ticket:
    """Combine the REST issue, issue fields, project item and native blockers."""
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
            "blocked_by": [entry["number"] for entry in blockers if entry.get("state") != "closed"],
            "components": components,
            "body_sections": parse_body_sections(issue.get("body") or ""),
        }
    )
