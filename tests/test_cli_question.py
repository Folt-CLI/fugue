"""Scripted walkthroughs without GitHub or provider calls."""

import asyncio
from collections.abc import Sequence
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console
from test_questions import BODY
from typer.testing import CliRunner

from fugue.agents import AccessMode, ScriptedAdapter, ScriptStep
from fugue.cli import app as cli_app
from fugue.cli import question as question_module
from fugue.cli.question import QuestionWalkthrough
from fugue.engine.facts import scope_url
from fugue.github.parse import parse_body_sections
from fugue.models import IssueType, Priority, Role, Size, Status, Ticket


class FakeClient:
    def __init__(self, body: str = BODY) -> None:
        self.body = body
        self.ticket = Ticket(
            number=2,
            title="Select the process",
            issue_type=IssueType.QUESTION,
            size=Size.L,
            status=Status.IN_ANALYSIS,
            priority=Priority.MEDIUM,
        )
        self.calls: list[tuple[str, int, str]] = []
        self.fail_edit = False

    async def get_ticket(self, number: int) -> Ticket:
        return self.ticket

    async def get_body(self, number: int) -> str:
        return self.body

    async def edit_body(self, number: int, text: str) -> None:
        if self.fail_edit:
            raise RuntimeError("Body edit failed")
        self.body = text
        self.calls.append(("edit", number, text))

    async def comment(self, number: int, body: str) -> None:
        self.calls.append(("comment", number, body))

    async def set_status(self, number: int, status: Status) -> None:
        self.calls.append(("status", number, status))


def walkthrough(
    client: FakeClient,
    commands: Sequence[str],
    adapter: ScriptedAdapter | None = None,
) -> tuple[QuestionWalkthrough, StringIO]:
    inputs = iter(commands)
    output = StringIO()

    def prompt(text: str) -> str:
        try:
            return next(inputs)
        except StopIteration as error:
            raise EOFError from error

    return QuestionWalkthrough(
        client,
        Path("/tmp"),
        adapter=adapter,
        prompt=prompt,
        print_fn=Console(file=output, width=120).print,
    ), output


def test_browse_compare_select() -> None:
    client = FakeClient()
    session, output = walkthrough(client, ["1", "c", "2", "s 2"])
    asyncio.run(session.run(2))
    assert "Existing workflow." in output.getvalue()
    assert "Compare options" in output.getvalue()
    assert "More code." in output.getvalue()
    assert [call[0] for call in client.calls] == ["edit", "comment", "status"]
    assert "[Selected Solution] [2-solution-2.md]" in client.body
    assert client.calls[1][2].startswith("Selected option 2: Extend the process.")
    assert client.calls[2] == ("status", 2, Status.READY_FOR_IMPLEMENTATION)


@pytest.mark.parametrize("commands", [["1", "c", "q"], [], ["q"]])
def test_quit_or_eof_does_not_mutate(commands: list[str]) -> None:
    client = FakeClient()
    session, _ = walkthrough(client, commands)
    asyncio.run(session.run(2))
    assert client.calls == []


def test_invalid_commands_recover_and_missing_adapter_skips() -> None:
    client = FakeClient()
    session, output = walkthrough(client, ["99", "s 0", "s nope", "?", "a", "q"])
    asyncio.run(session.run(2))
    assert "Unknown option 99" in output.getvalue()
    assert "Select an available option" in output.getvalue()
    assert "follow-up skipped" in output.getvalue()
    assert client.calls == []


def test_follow_up_sends_analysis_and_question_read_only() -> None:
    client = FakeClient()
    adapter = ScriptedAdapter([ScriptStep()])
    session, _ = walkthrough(client, ["a", "Which scope is smaller?", "q"], adapter)
    asyncio.run(session.run(2))
    request = adapter.requests[0]
    assert request.access == AccessMode.READ_ONLY
    assert request.role == Role.ANALYST
    assert "Which scope is smaller?" in request.prompt
    assert "### Option 2: Extend the process" in request.prompt
    assert "## Details" not in request.prompt
    assert client.calls == []


def test_non_question_refused() -> None:
    client = FakeClient()
    client.ticket.issue_type = IssueType.FEATURE
    session, _ = walkthrough(client, ["s 1"])
    with pytest.raises(ValueError, match="not a Question"):
        asyncio.run(session.run(2))
    assert client.calls == []


@pytest.mark.parametrize("selection", [0, 3])
def test_invalid_direct_selection_has_no_effect(selection: int) -> None:
    client = FakeClient()
    session, _ = walkthrough(client, [])
    with pytest.raises(ValueError, match="Unknown option"):
        asyncio.run(session.run(2, selection))
    assert client.calls == []


def test_missing_analysis_refused() -> None:
    client = FakeClient("## Analysis\nPending")
    session, _ = walkthrough(client, [])
    with pytest.raises(ValueError, match="no Analysis options"):
        asyncio.run(session.run(2))
    assert client.calls == []


def test_edit_failure_does_not_comment_or_change_status() -> None:
    client = FakeClient()
    client.fail_edit = True
    session, _ = walkthrough(client, ["s 1"])
    with pytest.raises(RuntimeError, match="Body edit failed"):
        asyncio.run(session.run(2))
    assert client.calls == []


@pytest.mark.parametrize("direct", [True, False])
def test_cli_registration_and_selection(
    direct: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = FakeClient()
    session, _ = walkthrough(client, ["s 1"])
    monkeypatch.setattr(question_module, "build_question", lambda config, root: session)
    arguments = ["question", "2", "--repo-root", str(tmp_path)]
    if direct:
        arguments.extend(["--select", "2"])
    result = CliRunner().invoke(cli_app.app, arguments)
    assert result.exit_code == 0, result.output
    expected = 2 if direct else 1
    assert f"[Selected Solution] [2-solution-{expected}.md]" in client.body


def test_cli_refuses_non_question(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = FakeClient()
    client.ticket.issue_type = IssueType.TASK
    session, _ = walkthrough(client, [])
    monkeypatch.setattr(question_module, "build_question", lambda config, root: session)
    result = CliRunner().invoke(cli_app.app, ["question", "2", "--repo-root", str(tmp_path)])
    assert result.exit_code == 2
    assert "not a Question" in result.output
    assert client.calls == []


def test_follow_up_without_read_only_capability_is_skipped() -> None:
    client = FakeClient()
    adapter = ScriptedAdapter([], capabilities=frozenset())
    session, output = walkthrough(client, ["a", "q"], adapter)
    asyncio.run(session.run(2))
    assert "lacks read-only mode" in output.getvalue()
    assert adapter.requests == []
    assert client.calls == []


@pytest.mark.parametrize("ok", [True, False])
def test_follow_up_answer_or_failure_is_printed_without_mutation(ok: bool) -> None:
    from fugue.agents import AgentResult
    from fugue.models import AuthenticationMode

    client = FakeClient()
    adapter = ScriptedAdapter(
        [
            ScriptStep(
                result=AgentResult(
                    ok=ok,
                    final_text="Choose the smaller scope.",
                    structured_output=None,
                    session_id=None,
                    usage=None,
                    provider="fake",
                    authentication_mode=AuthenticationMode.SUBSCRIPTION_LOGIN,
                    error="Provider unavailable",
                    timed_out=False,
                    cancelled=False,
                    log_path=None,
                    unsupported=None,
                )
            )
        ]
    )
    session, output = walkthrough(client, ["a", "Why?", "q"], adapter)
    asyncio.run(session.run(2))
    expected = "Choose the smaller scope." if ok else "Follow-up failed: Provider unavailable"
    assert expected in output.getvalue()
    assert client.calls == []


@pytest.mark.parametrize("reason", [None, "Need the walkthrough"])
def test_cli_fills_pending_solution_and_replaces_selection(
    reason: str | None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before, rest = BODY.split("## Solution\n")
    _, details = rest.split("## Details\n")
    client = FakeClient(before + "## Solution\n_Not selected yet._\n\n## Details\n" + details)
    session, _ = walkthrough(client, [])
    monkeypatch.setattr(question_module, "build_question", lambda config, root: session)
    arguments = ["question", "2", "--repo-root", str(tmp_path), "--select", "1"]
    result = CliRunner().invoke(cli_app.app, arguments)
    assert result.exit_code == 0, result.output
    first = client.body
    assert "**Why this one:** smaller scope." in first
    result = CliRunner().invoke(cli_app.app, arguments)
    assert result.exit_code == 0, result.output
    assert client.body == first
    arguments[-1] = "2"
    arguments.extend(["--by", "developer"])
    if reason is not None:
        arguments.extend(["--reason", reason])
    result = CliRunner().invoke(cli_app.app, arguments)
    assert result.exit_code == 0, result.output
    assert client.body.count("[Selected Solution] [") == 1
    assert (
        "[Selected Solution] [2-solution-2.md](https://github.com/user-attachments/two)"
        in client.body
    )
    assert f"**Why this one:** {reason or 'selected by developer'}" in client.body
    assert client.body.split("## Solution")[0] == before
    assert client.body.split("## Details\n")[1] == details
    assert [call[0] for call in client.calls] == ["edit", "comment", "status"] * 3


@pytest.mark.parametrize("initial", ["pending", "marked", "legacy", "unmarked"])
def test_cli_selection_keeps_engine_scope_in_sync(
    initial: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = BODY
    if initial == "pending":
        before, rest = body.split("## Solution\n")
        _, details = rest.split("## Details\n")
        body = before + "## Solution\n_Pending._\n\n## Details\n" + details
    elif initial in {"marked", "legacy"}:
        marker = (
            "[Selected Solution] [2-solution-1.md]"
            if initial == "marked"
            else "[[Selected Solution] 2-solution-1.md]"
        )
        body = body.replace(
            "## Solution\n**Codex scope:** [2-solution-1.md]",
            "## Solution\n**Codex scope:** " + marker,
        )
    client = FakeClient(body)
    session, _ = walkthrough(client, [])
    monkeypatch.setattr(question_module, "build_question", lambda config, root: session)
    for selection in (2, 1, 1, 2):
        result = CliRunner().invoke(
            cli_app.app,
            ["question", "2", "--repo-root", str(tmp_path), "--select", str(selection)],
        )
        assert result.exit_code == 0, result.output
        client.ticket.body_sections = parse_body_sections(client.body)
        url = "https://github.com/user-attachments/" + ("one" if selection == 1 else "two")
        assert scope_url(client.ticket) == url
        solution = client.ticket.body_sections["Solution"]
        assert solution.count("[Selected Solution] [") == 1
        assert f"[Selected Solution] [2-solution-{selection}.md]({url})" in solution
        assert "[[Selected Solution]" not in solution
