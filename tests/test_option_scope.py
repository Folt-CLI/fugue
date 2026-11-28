"""Two-phase selection: only the recommended option has a scope until another is selected."""

import asyncio
import hashlib
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_analysis import handoff
from test_engine_to_pr import result

from fugue.agents import AccessMode, ScriptedAdapter, ScriptStep
from fugue.analysis.option_scope import add_scope_link, scope_option
from fugue.analysis.render import render_analysis, render_attachments
from fugue.analysis.schema import Handoff
from fugue.config import FugueConfig
from fugue.github.attachments import UploadedAttachment
from fugue.models import IssueType, Priority, Role, Size, Status, Ticket
from fugue.questions.decision import record_decision
from fugue.questions.parse import parse_analysis

LINKS = {
    "43-code-findings.md": "https://github.com/user-attachments/files/1/findings.md",
    "43-solution-1.md": "https://github.com/user-attachments/files/2/scope.md",
}
SCOPE_JSON: dict[str, Any] = {
    "allowed_files": ["src/other.py"],
    "file_changes": ["src/other.py: the second approach"],
    "in_scope": ["The second approach works."],
    "verification": ["Run the tests."],
    "final_message": ["List the changes."],
}


def two_options() -> Handoff:
    data = handoff().model_dump(mode="json")
    data["options"].append(
        {
            "name": "Second approach",
            "you_get": ["Another result."],
            "changes": ["Change another thing."],
            "watch_out": ["More code."],
        }
    )
    data["comparison"] = [{"label": "Diff size", "values": ["Small", "Large"]}]
    return Handoff.model_validate(data)


def ticket() -> Ticket:
    return Ticket(
        number=43,
        title="Add analysis",
        issue_type=IssueType.QUESTION,
        size=Size.L,
        status=Status.IN_ANALYSIS,
        priority=Priority.MEDIUM,
    )


def body() -> str:
    analysis = render_analysis(43, two_options(), LINKS)
    head = "## Summary\n\nNeed it\n\n## Scope\n\nL\n\n"
    return f"{head}{analysis}\n## Solution\n\nPending\n\n## Details\n\nx\n"


def test_only_the_recommended_option_has_a_scope() -> None:
    value = two_options()
    assert set(render_attachments(43, value)) == {"43-code-findings.md", "43-solution-1.md"}
    options = parse_analysis(body()).options
    assert options[0].scope_url == LINKS["43-solution-1.md"]
    assert options[1].scope_url == ""


def test_the_link_goes_at_the_end_of_the_option_block_only() -> None:
    updated = add_scope_link(body(), 2, "43-solution-2.md", "https://github.com/user-attachments/x")
    option = parse_analysis(updated).option(2)
    assert (option.scope_name, option.scope_url) == (
        "43-solution-2.md",
        "https://github.com/user-attachments/x",
    )
    assert parse_analysis(updated).option(1) == parse_analysis(body()).option(1)
    assert updated.index("43-solution-2.md") < updated.index("### Recommendation")
    assert updated.endswith(body().split("### Recommendation")[1][-60:])
    with pytest.raises(ValueError, match="no option 9"):
        add_scope_link(body(), 9, "n", "u")


class Client:
    def __init__(self, *, wrong_digest: bool = False) -> None:
        self.wrong_digest = wrong_digest
        self.files: dict[str, bytes] = {}
        self.edits: list[str] = []
        self.comments: list[str] = []
        self.statuses: list[Status] = []

    async def upload_attachments(self, paths: list[Path]) -> list[UploadedAttachment]:
        path = paths[0]
        content = path.read_bytes()
        url = f"https://github.com/user-attachments/files/9/{path.name}"
        self.files[url] = content
        digest = "0" * 64 if self.wrong_digest else hashlib.sha256(content).hexdigest()
        return [UploadedAttachment(path.name, url, digest)]

    async def download_attachment(self, url: str, dest: Path) -> Path:
        dest.write_bytes(self.files[url])
        return dest

    async def get_ticket(self, number: int) -> Ticket:
        return ticket()

    async def get_body(self, number: int) -> str:
        return body()

    async def edit_body(self, number: int, text: str) -> None:
        self.edits.append(text)

    async def comment(self, number: int, body: str) -> None:
        self.comments.append(body)

    async def set_status(self, number: int, status: Status) -> None:
        self.statuses.append(status)


def analyst(output: dict[str, Any] | None = None) -> ScriptedAdapter:
    return ScriptedAdapter(
        [ScriptStep(result=replace(result(), structured_output=output or SCOPE_JSON))],
        name="claude",
    )


def test_selecting_an_unscoped_option_scopes_it_then_selects_it(tmp_path: Path) -> None:
    client, adapter = Client(), analyst()

    async def provide(text: str, option: Any) -> Any:
        return await scope_option(client, adapter, FugueConfig(), tmp_path, ticket(), text, option)

    chosen = parse_analysis(body()).option(2)
    asyncio.run(
        record_decision(client, 43, body(), chosen, reason="Smaller risk", scope_provider=provide)
    )
    request = adapter.requests[0]
    assert request.role == Role.ANALYST and request.access == AccessMode.READ_ONLY
    assert "option 2: Second approach" in request.prompt
    assert [name for name in client.files if name.endswith("43-solution-2.md")]
    selected = client.edits[-1]
    assert "[Selected Solution] [43-solution-2.md]" in selected
    assert "[Selected Solution] [43-solution-1.md]" not in selected
    assert client.comments[-1].startswith("Selected option 2: Second approach.")
    assert client.statuses == [Status.READY_FOR_IMPLEMENTATION]
    uploaded = next(iter(client.files.values())).decode()
    assert "src/other.py" in uploaded and "You may touch only these files" in uploaded


def test_the_recommended_option_is_selected_without_scoping(tmp_path: Path) -> None:
    client, adapter = Client(), analyst()

    async def provide(text: str, option: Any) -> Any:
        raise AssertionError("The recommended option already has a scope")

    chosen = parse_analysis(body()).option(1)
    asyncio.run(record_decision(client, 43, body(), chosen, scope_provider=provide))
    assert adapter.requests == [] and client.files == {}
    assert "[Selected Solution] [43-solution-1.md]" in client.edits[-1]


def test_an_upload_that_does_not_match_stops_the_selection(tmp_path: Path) -> None:
    client = Client(wrong_digest=True)
    chosen = parse_analysis(body()).option(2)
    with pytest.raises(ValueError, match=re.escape("does not match 43-solution-2.md")):
        asyncio.run(
            scope_option(client, analyst(), FugueConfig(), tmp_path, ticket(), body(), chosen)
        )
    assert client.edits == []


def test_a_failed_scoping_selects_nothing(tmp_path: Path) -> None:
    client = Client()
    failing = ScriptedAdapter(
        [ScriptStep(result=replace(result(ok=False, error="Provider failed"), provider="claude"))],
        name="claude",
    )

    async def provide(text: str, option: Any) -> Any:
        return await scope_option(client, failing, FugueConfig(), tmp_path, ticket(), text, option)

    with pytest.raises(ValueError, match="Provider failed"):
        asyncio.run(
            record_decision(
                client, 43, body(), parse_analysis(body()).option(2), scope_provider=provide
            )
        )
    assert client.edits == [] and client.comments == [] and client.statuses == []
