"""Check a ticket draft against the repository policy before anything is created."""

from collections.abc import Collection
from pathlib import Path, PurePosixPath

from fugue.config import RepoPolicy
from fugue.models import Size, Status, Ticket
from fugue.tickets.draft import TicketDraft
from fugue.tickets.render import SCOPE_SENTENCE, render_scope
from fugue.workflow.guards import components_ok, ready_for_implementation


def _path_problems(draft: TicketDraft, repo_root: Path | None) -> list[str]:
    found: list[str] = []
    new = set(draft.new_files)
    for path in draft.allowed_files:
        posix = PurePosixPath(path)
        if posix.is_absolute() or path.startswith(("/", "\\")) or "\\" in path:
            found.append(f"Allowed file must be a relative path: {path}")
        elif ".." in posix.parts:
            found.append(f"Allowed file must not contain '..': {path}")
        elif "`" in path or not path.strip():
            found.append(f"Allowed file is not a valid path: {path}")
        elif repo_root is not None and path not in new and not (repo_root / path).exists():
            found.append(f"Allowed file does not exist and is not marked new: {path}")
    return found


def problems(
    draft: TicketDraft,
    policy: RepoPolicy,
    known_numbers: Collection[int],
    *,
    milestones: Collection[str] | None = None,
    open_numbers: Collection[int] | None = None,
    repo_root: Path | None = None,
) -> list[str]:
    """Every reason the draft cannot be created; empty when it can.

    `known_numbers` holds the issue numbers that exist; `open_numbers`, when given, the
    ones that are open, which blockers must be.
    """
    found: list[str] = []
    if not draft.title.strip():
        found.append("Title must be non-empty")
    if not draft.summary.strip():
        found.append("Summary must be non-empty")
    if not draft.scope_text.strip():
        found.append("Scope must be non-empty")
    if draft.size == Size.XL:
        found.append("Size XL is not supported; Epic work is out of scope")
        return found
    if milestones is not None and draft.milestone not in milestones:
        found.append(f"Milestone does not exist: {draft.milestone}")
    if draft.parent not in known_numbers:
        found.append(f"Parent #{draft.parent} does not exist")
    for blocker in draft.blocked_by:
        if blocker not in known_numbers:
            found.append(f"Blocker #{blocker} does not exist")
        elif open_numbers is not None and blocker not in open_numbers:
            found.append(f"Blocker #{blocker} is not open")
    if draft.size in (Size.XS, Size.S):
        if len(draft.components) != 1:
            found.append(f"Size {draft.size} requires exactly one Component")
        if draft.title.strip():
            ticket = Ticket(
                number=1,
                title=draft.title,
                issue_type=draft.effective_type,
                size=draft.size,
                status=Status.TO_DO,
                priority=draft.priority,
                components=draft.components,
                body_sections={
                    "Summary": draft.summary,
                    "Scope": draft.scope_text,
                    "Solution": draft.decision,
                },
            )
            found.extend(
                item
                for item in ready_for_implementation(
                    ticket, policy, has_scope_attachment=True, selected_solution=True
                )
                if not item.startswith(("Summary", "Scope"))
            )
        if draft.size == Size.S and not draft.allowed_files:
            found.append("Size S requires allowed files")
        found.extend(_path_problems(draft, repo_root))
        if draft.allowed_files and not _path_problems(draft, None):
            try:
                render_scope(draft, 1)
            except ValueError:
                found.append(f"Scope must list its files after '{SCOPE_SENTENCE}'")
    else:
        found.extend(components_ok(draft.size, draft.components, policy))
        found.extend(_path_problems(draft, repo_root))
    unique: list[str] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique
