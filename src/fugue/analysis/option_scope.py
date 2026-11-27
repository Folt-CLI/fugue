"""The second phase of a selection: scope an option nobody recommended, once it is selected."""

import hashlib
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from fugue.agents import AccessMode, AgentRequest, ProviderAdapter, resolve_models
from fugue.analysis.render import render_scope, scope_name
from fugue.analysis.schema import Scope
from fugue.analysis.scoping import option_scope_prompt
from fugue.analysis.stage import parent_decision
from fugue.config import FugueConfig
from fugue.github.attachments import UploadedAttachment
from fugue.models import Role, Ticket
from fugue.questions.parse import Option, parse_analysis, prose_only

_NEXT_HEADING = re.compile(r"^#{2,3}[ \t]+", re.MULTILINE)


class OptionScopeClient(Protocol):
    async def upload_attachments(self, paths: list[Path]) -> list[UploadedAttachment]: ...

    async def download_attachment(self, url: str, dest: Path) -> Path: ...

    async def edit_body(self, number: int, text: str) -> None: ...


def add_scope_link(body: str, option: int, name: str, url: str) -> str:
    """Add the scope link at the end of the option's block, leaving everything else as it is."""
    masked = prose_only(body)
    heading = re.search(rf"^###[ \t]+Option {option}:[^\n]*\n", masked, re.MULTILINE)
    if heading is None:
        raise ValueError(f"Analysis has no option {option}")
    following = _NEXT_HEADING.search(masked, heading.end())
    end = following.start() if following else len(body)
    block = body[heading.end() : end].rstrip("\n")
    link = f"**Implementation scope:** [{name}]({url})"
    return body[: heading.end()] + block + f"\n\n{link}\n\n" + body[end:].lstrip("\n")


async def scope_option(
    client: OptionScopeClient,
    adapter: ProviderAdapter,
    config: FugueConfig,
    cwd: Path,
    ticket: Ticket,
    body: str,
    option: Option,
) -> tuple[str, Option]:
    """Write, upload and link the scope of `option`; return the new body and the option."""
    analysis = parse_analysis(body)
    decision = await parent_decision(client, ticket) if hasattr(client, "get_ticket") else ""
    choice = resolve_models(config, Role.ANALYST, ticket.size)
    role = config.roles[Role.ANALYST]
    result = await adapter.run(
        AgentRequest(
            role=Role.ANALYST,
            prompt=option_scope_prompt(ticket, analysis.text, option.number, option.name, decision),
            cwd=cwd,
            access=AccessMode.READ_ONLY,
            output_schema=Scope.model_json_schema(),
            timeout_seconds=config.limits.agent_timeout_seconds,
            model=choice.models.get(role.provider),
            models=choice.models,
        )
    )
    if not result.ok or result.structured_output is None:
        raise ValueError(result.error or f"Scoping option {option.number} did not complete")
    scope = Scope.model_validate(result.structured_output)
    name = scope_name(ticket.number, option.number)
    text = render_scope(ticket.number, option.number, option.name, scope)
    digest = hashlib.sha256(text.encode()).hexdigest()
    with TemporaryDirectory(prefix="fugue-option-scope-") as directory:
        path = Path(directory) / name
        path.write_text(text, encoding="utf-8")
        uploaded = await client.upload_attachments([path])
        if len(uploaded) != 1 or uploaded[0].name != name or uploaded[0].sha256 != digest:
            raise ValueError(f"Upload response does not match {name}")
        attachment = uploaded[0]
        if not attachment.href.startswith("https://github.com/user-attachments/"):
            raise ValueError(f"Upload verification mismatch for {name}")
        copy = await client.download_attachment(attachment.href, Path(directory) / "check.md")
        if hashlib.sha256(copy.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Upload verification mismatch for {name}")
    updated = add_scope_link(body, option.number, name, attachment.href)
    await client.edit_body(ticket.number, updated)
    return updated, parse_analysis(updated).option(option.number)
