"""Render a ticket draft as an issue body, a scope attachment and a conversation attachment."""

from datetime import UTC, date, datetime

from fugue.engine.facts import allowed_files, slug
from fugue.models import Size
from fugue.tickets.draft import TicketDraft, redact

PENDING = "_Pending._"
SCOPE_SENTENCE = "You may touch only these files"


def marker_for(draft: TicketDraft) -> str:
    return f"<!-- fugue:new:{draft.id} -->"


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- None."


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1)) or "1. None."


def _solution(draft: TicketDraft, scope_link: str) -> str:
    agreed = (
        f"**Agreed in #{draft.parent}**"
        if draft.parent_is_question
        else "**Agreed in the ticket conversation**"
    )
    text = (
        f"> [!IMPORTANT]\n> {draft.decision}\n\n"
        f"{agreed}\n\n{_bullets(draft.agreed)}\n\n"
        f"**You get**\n\n{_bullets(draft.you_get)}\n\n"
        f"**Changes**\n\n{_bullets(draft.changes)}\n\n"
        f"**Not changed**\n\n{_bullets(draft.not_changed)}\n\n"
        f"**Watch out**\n\n{_bullets(draft.watch_out)}"
    )
    if scope_link:
        text += f"\n\n**Implementation scope:** {scope_link}"
    return text


def render_body(
    draft: TicketDraft, scope_link: str = "", conversation_link: str = "", marker: str = ""
) -> str:
    """The five work-item sections; links are Markdown links, empty before an upload."""
    if draft.size in (Size.XS, Size.S):
        analysis = "No analysis needed."
        solution = _solution(draft, scope_link)
    else:
        analysis = PENDING
        solution = PENDING
    details = f"**Conversation:** {conversation_link}" if conversation_link else "Created by fugue."
    body = (
        f"## Summary\n\n{draft.summary}\n\n## Scope\n\n{draft.scope_text}\n\n"
        f"## Analysis\n\n{analysis}\n\n## Solution\n\n{solution}\n\n"
        f"## Details\n\n{details}\n"
    )
    return f"{body}\n{marker}\n" if marker else body


def render_scope(draft: TicketDraft, number: int) -> str:
    """The `<N>-solution-1.md` scope, in the layout of the issue format."""
    new = set(draft.new_files)
    files = "\n".join(f"- `{path}`" for path in draft.allowed_files)
    changes = "\n".join(
        f"- `{path}`: {'new file' if path in new else 'existing file'}"
        for path in draft.allowed_files
    )
    branch = f"{draft.effective_type.lower()}/{number}-{slug(draft.title)}"
    text = (
        f"# {number} solution 1: {draft.title}\n\n"
        "## Standing rules\n\n"
        "- Implement only. No Git commit, push or `gh` command.\n"
        f"- Git branch: `{branch}`.\n"
        "- No attribution lines, no provider or model names and no emojis.\n"
        "- Read GLOSSARY.md first and use its terms exactly as defined.\n"
        f"- {SCOPE_SENTENCE} (create them if new):\n"
        f"{files}\n\n"
        f"## File-by-file changes\n\n"
        f"**Scope**\n\n{draft.scope_text}\n\n"
        f"{_solution(draft, '')}\n\n"
        f"**Files**\n\n{changes}\n\n"
        f"## In-scope scenarios\n\n{_numbered(draft.in_scope_scenarios)}\n\n"
        f"## Out-of-scope scenarios\n\n{_numbered(draft.out_of_scope_scenarios)}\n\n"
        "## Do not change\n\n- Any file not listed in the allowed files.\n"
        f"{_bullets(draft.not_changed)}\n\n"
        "## Verification\n\n- Run the repository gate; every command must pass.\n\n"
        "## Final message\n\n"
        "- List the files changed, each verification result and the scenarios proven.\n"
    )
    if allowed_files(text) != draft.allowed_files:
        raise ValueError("The allowed files do not round-trip through the rendered scope")
    return text


def render_conversation(draft: TicketDraft, number: int | None, today: date | None = None) -> str:
    """UTF-8 Markdown of the verbatim turns, the final preview and the decision."""
    when = today if today is not None else datetime.now(UTC).date()
    heading = f"ticket #{number}" if number is not None else "a new ticket"
    lines = [f"# Conversation for {heading}", "", f"Date: {when.isoformat()}", ""]
    for turn in draft.turns:
        lines += [f"{turn.speaker}: {turn.text}", ""]
    decision = "confirmed with yes" if draft.confirmed else "not confirmed"
    lines += [
        "## Final preview",
        "",
        f"Title: {draft.title}",
        "",
        render_body(draft),
        "## Decision",
        "",
        f"The person {decision}.",
        "",
    ]
    return redact("\n".join(lines))
