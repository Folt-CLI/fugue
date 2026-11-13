"""Pure facts derived from a ticket and its scope."""

import re
import unicodedata
from pathlib import Path

from fugue.models import Ticket

SOLUTION_LINK_RE = re.compile(
    r"\[(\d+)-solution-(\d+)\.md\]\((https://github\.com/user-attachments/[^\s)]+)\)"
)
SELECTED_MARKER = "[Selected Solution]"
MARKER_WINDOW = 40


def _marked_selected(body: str, match: re.Match[str]) -> bool:
    line_start = body.rfind("\n", 0, match.start()) + 1
    before = body[line_start : match.start()][-MARKER_WINDOW:]
    return before.rstrip().endswith(SELECTED_MARKER)


def scope_url(ticket: Ticket) -> str | None:
    body = ticket.body_sections.get("Solution", "")
    matches = [m for m in SOLUTION_LINK_RE.finditer(body) if int(m[1]) == ticket.number]
    if not matches:
        return None
    selected = [m for m in matches if _marked_selected(body, m)]
    return (selected or matches)[-1][3]


def selected_solution(ticket: Ticket) -> bool:
    return "[Selected Solution]" in ticket.body_sections.get("Solution", "")


def allowed_files(scope_text: str) -> list[str]:
    paths: list[str] = []
    lines = iter(scope_text.splitlines())
    for line in lines:
        if "You may touch only these files" in line:
            break
    else:
        return paths
    for line in lines:
        if not re.match(r"^\s*[-*+]\s+", line):
            break
        paths.extend(re.findall(r"`([^`]+)`", line))
    return paths


def slug(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:40].rstrip("-")


def branch_name(ticket: Ticket) -> str:
    return f"{ticket.issue_type.lower()}/{ticket.number}-{slug(ticket.title)}"


def worktree_path(root: Path, ticket: Ticket) -> Path:
    return root / f"{ticket.number}-{slug(ticket.title)}"


def explanation(ticket: Ticket) -> str:
    prefix = f"{ticket.issue_type}[#{ticket.number}]: "
    text = " ".join(ticket.title.split()).rstrip(". ")
    text = text[:1].upper() + text[1:]
    return text[: max(0, 72 - len(prefix))].rstrip(". ")
