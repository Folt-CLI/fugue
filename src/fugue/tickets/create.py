"""Create the issue from a confirmed draft in journalled steps that a rerun resumes."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from fugue.github.attachments import UploadedAttachment
from fugue.models import Component, IssueType, Priority, Size, Status
from fugue.tickets.draft import TicketDraft
from fugue.tickets.drafts import DraftStore
from fugue.tickets.render import (
    marker_for,
    render_body,
    render_conversation,
    render_scope,
)


class CreationPort(Protocol):
    repo: str

    async def find_child(self, parent: int, marker: str) -> int | None: ...

    async def create_issue(
        self,
        title: str,
        body: str,
        issue_type: IssueType,
        parent: int,
        milestone: str,
        assignee: str,
        *,
        add_to_project: bool = True,
    ) -> int: ...

    async def ensure_project(self, number: int) -> None: ...

    async def set_size(self, number: int, size: Size) -> None: ...

    async def set_status(self, number: int, status: Status) -> None: ...

    async def set_priority(self, number: int, priority: Priority) -> None: ...

    async def set_components(self, number: int, components: Sequence[Component]) -> None: ...

    async def upload_attachments(self, paths: Sequence[Path]) -> list[UploadedAttachment]: ...

    async def edit_issue_body(self, number: int, body: str) -> None: ...

    async def block(self, number: int, blocker: int) -> None: ...


@dataclass(frozen=True)
class Created:
    number: int
    url: str
    next_command: str


def _link(item: dict[str, str]) -> str:
    return f"[{item['name']}]({item['href']})"


async def create_ticket(draft: TicketDraft, client: CreationPort, drafts: DraftStore) -> Created:
    """Run each step not yet in the journal and journal it, saving the draft after each.

    A failure leaves the journal as far as it got; the same call resumes from there and
    finds an issue created earlier by its marker rather than creating a second one.
    """
    marker = marker_for(draft)

    async def step(name: str, action: Callable[[], Awaitable[object]]) -> object:
        if name in draft.journal:
            return draft.journal[name]
        result = await action()
        draft.journal[name] = result if result is not None else True
        drafts.save(draft)
        return draft.journal[name]

    async def make_issue() -> int:
        found = await client.find_child(draft.parent, marker)
        if found is not None:
            return found
        return await client.create_issue(
            draft.title,
            render_body(draft, marker=marker),
            draft.effective_type,
            draft.parent,
            draft.milestone,
            draft.assignee,
            add_to_project=False,
        )

    number = int(str(await step("issue", make_issue)))
    if draft.created_number != number:
        draft.created_number = number
        drafts.save(draft)
    scoped = draft.size in (Size.XS, Size.S)
    await step("project", lambda: client.ensure_project(number))
    await step("size", lambda: client.set_size(number, draft.size))
    await step("status", lambda: client.set_status(number, Status.TO_DO))
    await step("priority", lambda: client.set_priority(number, draft.priority))
    if scoped:
        await step("components", lambda: client.set_components(number, draft.components))

    async def upload() -> dict[str, dict[str, str]]:
        with TemporaryDirectory(prefix="fugue-new-") as directory:
            paths: list[Path] = []
            if scoped:
                scope = Path(directory) / f"{number}-solution-1.md"
                scope.write_text(render_scope(draft, number), encoding="utf-8")
                paths.append(scope)
            conversation = Path(directory) / f"{number}-conversation.md"
            conversation.write_text(render_conversation(draft, number), encoding="utf-8")
            paths.append(conversation)
            uploaded = await client.upload_attachments(paths)
        return {item.name: {"name": item.name, "href": item.href} for item in uploaded}

    attachments = await step("attachments", upload)
    assert isinstance(attachments, dict)
    scope_link = _link(attachments[f"{number}-solution-1.md"]) if scoped else ""
    conversation_link = _link(attachments[f"{number}-conversation.md"])
    body = render_body(draft, scope_link, conversation_link, marker)
    await step("body", lambda: client.edit_issue_body(number, body))
    for blocker in draft.blocked_by:
        await step(f"block:{blocker}", partial(client.block, number, blocker))
    return Created(
        number=number,
        url=f"https://github.com/{client.repo}/issues/{number}",
        next_command=f"fugue run {number}",
    )
