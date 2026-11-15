"""Scripted walkthroughs without GitHub or provider calls."""

import asyncio
import sys
from collections.abc import Callable, Sequence
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console
from test_questions import BODY
from typer.testing import CliRunner

from fugue.agents import AccessMode, ScriptedAdapter, ScriptStep
from fugue.cli import app as cli_app
from fugue.cli import question as question_module
from fugue.cli import terminal
from fugue.cli.choices import ChoiceItem
from fugue.cli.question import QuestionWalkthrough
from fugue.engine.facts import scope_url
from fugue.github.parse import parse_body_sections
from fugue.models import IssueType, Priority, Role, Size, Status, Ticket
from fugue.questions.parse import parse_analysis

runner = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"})


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
        print_fn=Console(file=output, width=200, no_color=True, force_terminal=False).print,
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


def test_non_question_selects_analysed_option() -> None:
    client = FakeClient()
    client.ticket.issue_type = IssueType.FEATURE
    session, output = walkthrough(client, [])
    asyncio.run(session.run(2, 1))
    assert "Ticket #2: Select the process" in output.getvalue()
    assert [call[0] for call in client.calls] == ["edit", "comment", "status"]
    assert "[Selected Solution] [2-solution-1.md]" in client.body
    assert client.calls[2] == ("status", 2, Status.READY_FOR_IMPLEMENTATION)


def test_non_question_without_options_refused() -> None:
    client = FakeClient("## Analysis\nPending")
    client.ticket.issue_type = IssueType.FEATURE
    session, _ = walkthrough(client, [])
    with pytest.raises(ValueError, match="Ticket #2 has no Analysis options"):
        asyncio.run(session.run(2, 1))
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
    result = runner.invoke(cli_app.app, arguments)
    assert result.exit_code == 0, result.output
    expected = 2 if direct else 1
    assert f"[Selected Solution] [2-solution-{expected}.md]" in client.body


def test_cli_refuses_non_question_without_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = FakeClient("## Analysis\nPending")
    client.ticket.issue_type = IssueType.TASK
    session, _ = walkthrough(client, [])
    monkeypatch.setattr(question_module, "build_question", lambda config, root: session)
    result = runner.invoke(cli_app.app, ["question", "2", "--repo-root", str(tmp_path)])
    assert result.exit_code == 2
    assert "has no Analysis options" in result.output
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
    result = runner.invoke(cli_app.app, arguments)
    assert result.exit_code == 0, result.output
    first = client.body
    assert "**Why this one:** smaller scope." in first
    result = runner.invoke(cli_app.app, arguments)
    assert result.exit_code == 0, result.output
    assert client.body == first
    arguments[-1] = "2"
    arguments.extend(["--by", "developer"])
    if reason is not None:
        arguments.extend(["--reason", reason])
    result = runner.invoke(cli_app.app, arguments)
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
        result = runner.invoke(
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


@pytest.mark.parametrize("kind", ["select", "non_question", "analysed"])
def test_cli_rejects_invalid_automatic_without_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    client = FakeClient()
    if kind == "non_question":
        client.ticket.issue_type = IssueType.FEATURE
    session, _ = walkthrough(client, [])
    monkeypatch.setattr(question_module, "build_question", lambda config, root: session)

    def forbidden(*args: object) -> None:
        raise AssertionError("Engine must not be built")

    monkeypatch.setattr(cli_app, "engine_factory", forbidden)
    arguments = ["question", "2", "--repo-root", str(tmp_path), "--automatic"]
    if kind == "select":
        arguments += ["--select", "1"]
    outcome = runner.invoke(cli_app.app, arguments)
    assert outcome.exit_code == 2, outcome.output
    assert client.calls == []


@pytest.mark.parametrize(
    "ending", ["proceed", "quit", "needs_human", "limited", "failed", "locked"]
)
def test_cli_pending_interview_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ending: str,
) -> None:
    from contextlib import nullcontext

    from test_analysis import make_deps
    from test_interview_session import result

    from fugue.analysis.render import render_body
    from fugue.cli.app import _StoreClosingEngine
    from fugue.engine import Engine
    from fugue.engine.locks import TicketLock

    deps, github, analysis_adapter = make_deps(tmp_path, size=Size.L)
    github.ticket.issue_type = IssueType.QUESTION

    class Bridge(FakeClient):
        async def get_ticket(self, number: int) -> Ticket:
            return github.ticket

        async def get_body(self, number: int) -> str:
            sections = github.ticket.body_sections
            return render_body(
                sections,
                "## Analysis\n" + sections.get("Analysis", "Pending"),
                "## Details\n" + sections.get("Details", "Pending"),
            )

        async def edit_body(self, number: int, text: str) -> None:
            await github.edit_body(number, text)

        async def set_status(self, number: int, status: Status) -> None:
            await github.set_status(number, status)

    client = Bridge()
    if ending == "proceed":
        steps = [ScriptStep(result=result()), *analysis_adapter._steps]
    elif ending in {"limited", "failed"}:
        steps = [
            ScriptStep(
                result=result(ok=False, provider_limited=ending == "limited", error="Unavailable")
            )
        ]
    else:
        steps = [ScriptStep(result=result())]
    analyst = ScriptedAdapter(steps)
    deps.registry.register("claude", lambda config: analyst)
    if ending == "needs_human":
        standin = ScriptedAdapter(
            [
                ScriptStep(
                    result=result(
                        structured_output={
                            "reply": "",
                            "proceed": False,
                            "needs_human": True,
                            "reason": "Need consent",
                        }
                    )
                )
            ]
        )
        deps.registry.register("codex", lambda config: standin)
    commands = ["/proceed", "s 1"] if ending == "proceed" else ["/quit"]
    session, _ = walkthrough(client, commands)
    monkeypatch.setattr(question_module, "build_question", lambda config, root: session)
    monkeypatch.setattr(cli_app, "engine_factory", lambda *args: _StoreClosingEngine(Engine(deps)))
    arguments = ["question", "43", "--repo-root", str(tmp_path)]
    if ending == "needs_human":
        arguments.append("--automatic")
    context = TicketLock(tmp_path, github.repo, 43) if ending == "locked" else nullcontext()
    with context:
        outcome = runner.invoke(cli_app.app, arguments)
    expected = {"proceed": 0, "quit": 6, "needs_human": 3, "limited": 5, "failed": 4, "locked": 3}
    assert outcome.exit_code == expected[ending], outcome.output
    if ending == "proceed":
        assert github.ticket.status == Status.READY_FOR_IMPLEMENTATION
        assert "[Selected Solution]" in github.ticket.body_sections["Solution"]
    else:
        assert github.uploads == []
        assert github.ticket.status == Status.TO_DO
    if ending == "locked":
        assert "already being run" in outcome.output
        assert analyst.requests == []


class FakeChooser:
    def __init__(self, actions: Sequence[int | BaseException | None], *, yes: bool = True) -> None:
        self.actions = iter(actions)
        self.yes = yes
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.confirmations: list[str] = []

    def choose(
        self, message: str, choices: Sequence[str | ChoiceItem], default: int = 0
    ) -> int | None:
        self.calls.append(
            (
                message,
                tuple(
                    choice.label if isinstance(choice, ChoiceItem) else choice for choice in choices
                ),
            )
        )
        action = next(self.actions)
        if isinstance(action, BaseException):
            raise action
        return action

    def choose_many(
        self,
        message: str,
        items: Sequence[str | ChoiceItem],
        defaults: Sequence[int] = (),
        *,
        allow_empty: bool = False,
    ) -> list[int] | None:
        raise AssertionError("The Question walkthrough must not request multiple choices")

    def confirm(self, message: str, default: bool = False) -> bool:
        self.confirmations.append(message)
        return self.yes


def menu_walkthrough(
    monkeypatch: pytest.MonkeyPatch,
    client: FakeClient,
    chooser: FakeChooser,
    commands: Sequence[str] = (),
    adapter: ScriptedAdapter | None = None,
) -> tuple[QuestionWalkthrough, StringIO]:
    async def fake_prompt_call(function: Callable[..., object], *args: object) -> object:
        # The fake chooser has no terminal event loop to isolate in a worker.
        return function(*args)

    monkeypatch.setattr(asyncio, "to_thread", fake_prompt_call)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(terminal, "is_interactive", lambda console: True)
    session, output = walkthrough(client, commands, adapter)
    session._chooser = chooser
    return session, output


def test_menu_browse_compare_follow_up_and_quit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    adapter = ScriptedAdapter([ScriptStep()])
    chooser = FakeChooser([0, 1, 2, 3, 6])
    session, output = menu_walkthrough(monkeypatch, client, chooser, ["Why?"], adapter)
    asyncio.run(session.run(2))
    assert chooser.calls[0][1][0] == "Option 1: Keep the process (recommended)"
    assert chooser.calls[0][1][1] == "Option 2: Extend the process"
    assert "Existing workflow." in output.getvalue()
    assert "More code." in output.getvalue()
    assert "Compare options" in output.getvalue()
    assert len(adapter.requests) == 1
    assert adapter.requests[0].access == AccessMode.READ_ONLY
    assert "Why?" in adapter.requests[0].prompt
    assert client.calls == []
    assert client.body == BODY
    assert "next:" not in output.getvalue()


@pytest.mark.parametrize("reason", ["", "Fits our needs"])
def test_menu_confirms_and_records_once(monkeypatch: pytest.MonkeyPatch, reason: str) -> None:
    client = FakeClient()
    chooser = FakeChooser([4, 1])
    session, output = menu_walkthrough(monkeypatch, client, chooser, [reason])
    prompts: list[str] = []
    reader = session.prompt

    def prompt(text: str) -> str:
        prompts.append(text)
        return reader(text)

    session.prompt = prompt
    asyncio.run(session.run(2, by="developer"))
    assert chooser.confirmations == ["Select option 2: Extend the process?"]
    assert prompts == ["Reason (optional): "]
    assert [call[0] for call in client.calls] == ["edit", "comment", "status"]
    assert f"**Why this one:** {reason or 'selected by developer'}" in client.body
    assert output.getvalue().splitlines()[-1] == "next: fugue children 2"
    assert output.getvalue().count("next:") == 1


def test_menu_declined_confirmation_does_not_record(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    chooser = FakeChooser([4, 1, 6], yes=False)
    session, _ = menu_walkthrough(monkeypatch, client, chooser)

    def forbidden(text: str) -> str:
        raise AssertionError("Declined selection must not prompt for a reason")

    session.prompt = forbidden
    asyncio.run(session.run(2))
    assert client.calls == []
    assert client.body == BODY


@pytest.mark.parametrize("stage", ["menu", "option", "confirm", "reason"])
@pytest.mark.parametrize("ending", [EOFError, KeyboardInterrupt])
def test_menu_interruption_leaves_ticket_unchanged(
    monkeypatch: pytest.MonkeyPatch, stage: str, ending: type[BaseException]
) -> None:
    client = FakeClient()
    actions_by_stage: dict[str, list[int | BaseException | None]] = {
        "menu": [ending()],
        "option": [4, ending()],
        "confirm": [4, 1],
        "reason": [4, 1],
    }
    chooser = FakeChooser(actions_by_stage[stage])
    session, _ = menu_walkthrough(monkeypatch, client, chooser)

    def interrupted_prompt(text: str) -> str:
        raise ending()

    def interrupted_confirm(message: str, default: bool = False) -> bool:
        raise ending()

    if stage == "confirm":
        monkeypatch.setattr(chooser, "confirm", interrupted_confirm)
    session.prompt = interrupted_prompt
    asyncio.run(session.run(2))
    assert client.calls == []
    assert client.body == BODY


@pytest.mark.parametrize("actions", [[None], [4, None]])
def test_menu_cancel_leaves_ticket_unchanged(
    monkeypatch: pytest.MonkeyPatch, actions: list[int | None]
) -> None:
    client = FakeClient()
    session, _ = menu_walkthrough(monkeypatch, client, FakeChooser(actions))
    asyncio.run(session.run(2))
    assert client.calls == []


def test_cli_direct_selection_never_prompts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = FakeClient()
    chooser = FakeChooser([])
    session, output = menu_walkthrough(monkeypatch, client, chooser)

    def forbidden(text: str) -> str:
        raise AssertionError("Direct selection must not prompt")

    session.prompt = forbidden
    monkeypatch.setattr(question_module, "build_question", lambda config, root: session)
    result = runner.invoke(
        cli_app.app,
        [
            "question",
            "2",
            "--repo-root",
            str(tmp_path),
            "--select",
            "2",
            "--reason",
            "Fits our needs",
            "--by",
            "developer",
        ],
    )
    assert result.exit_code == 0, result.output
    assert chooser.calls == []
    assert chooser.confirmations == []
    assert "Show full analysis" not in output.getvalue()
    assert "### Recommendation" in output.getvalue()
    assert [call[0] for call in client.calls] == ["edit", "comment", "status"]
    assert "**Why this one:** Fits our needs" in client.body
    assert output.getvalue().splitlines()[-1] == "next: fugue children 2"


def test_non_terminal_keeps_typed_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    adapter = ScriptedAdapter([ScriptStep()])
    chooser = FakeChooser([])
    session, output = walkthrough(client, ["1", "c", "a", "Why?", "s 2"], adapter)
    session._chooser = chooser
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    asyncio.run(session.run(2))
    assert chooser.calls == []
    analysis = parse_analysis(BODY)
    assert output.getvalue().startswith(
        f"# Question #2: Select the process\n\n{analysis.overview}\n"
        "1: Keep the process\n2: Extend the process\n"
        f"### Recommendation\n\n{analysis.recommendation}\n"
    )
    assert "Show full analysis" not in output.getvalue()
    assert "(recommended)" not in output.getvalue()
    assert "Existing workflow." in output.getvalue()
    assert "Compare options" in output.getvalue()
    assert len(adapter.requests) == 1
    assert [call[0] for call in client.calls] == ["edit", "comment", "status"]
    assert output.getvalue().splitlines()[-1] == "next: fugue children 2"


@pytest.mark.parametrize("recommendation", ["Option 1: smaller scope.", "", "Option 99: absent."])
def test_menu_summary_precedes_chooser(
    monkeypatch: pytest.MonkeyPatch, recommendation: str
) -> None:
    client = FakeClient(BODY.replace("Option 1: smaller scope.", recommendation))
    chooser = FakeChooser([None])
    session, output = menu_walkthrough(monkeypatch, client, chooser)
    choose = chooser.choose

    def inspect_summary(
        message: str, choices: Sequence[str | ChoiceItem], default: int = 0
    ) -> int | None:
        text = output.getvalue()
        assert "Question #2: Select the process" in text
        assert "\n".join(parse_analysis(client.body).overview.splitlines()[:3]) in text
        assert "..." in text
        assert "1. Keep the process" in text
        assert "2. Extend the process" in text
        assert ("Keep the process (recommended)" in text) == bool(
            recommendation.startswith("Option 1:")
        )
        assert "Diff size" not in text
        assert "Recommendation" not in text
        assert "Show full analysis" in choices
        return choose(message, choices, default)

    monkeypatch.setattr(chooser, "choose", inspect_summary)
    asyncio.run(session.run(2))
    assert client.calls == []


def test_menu_full_analysis_prints_complete_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    chooser = FakeChooser([5, 6])
    session, output = menu_walkthrough(monkeypatch, client, chooser)
    asyncio.run(session.run(2))
    text = output.getvalue()
    assert parse_analysis(BODY).text in text
    assert "### Recommendation\nOption 1: smaller scope." in text
    assert "## Solution" not in text
    assert "## Details" not in text
    assert len(chooser.calls) == 2
    assert client.calls == []
