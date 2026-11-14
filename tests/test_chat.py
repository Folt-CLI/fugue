"""Chat component: streaming, input, slash commands and transcript."""

import asyncio
from collections.abc import Sequence
from io import StringIO
from pathlib import Path

from rich.console import Console

from fugue.agents import (
    AccessMode,
    AgentEvent,
    AgentRequest,
    AgentResult,
    ScriptedAdapter,
    ScriptStep,
)
from fugue.cli.chat import ChatSession
from fugue.models import AuthenticationMode, Role


def result(*, ok: bool = True, text: str = "", error: str | None = None) -> AgentResult:
    return AgentResult(
        ok=ok,
        final_text=text,
        structured_output=None,
        session_id=None,
        usage=None,
        provider="fake",
        authentication_mode=AuthenticationMode.SUBSCRIPTION_LOGIN,
        error=error,
        timed_out=False,
        cancelled=False,
        log_path=None,
        unsupported=None,
    )


def request(text: str) -> AgentRequest:
    return AgentRequest(
        role=Role.ANALYST,
        prompt=text,
        cwd=Path("/tmp"),
        access=AccessMode.READ_ONLY,
        timeout_seconds=5,
    )


def text_events(*chunks: str) -> list[AgentEvent]:
    return [AgentEvent("text", chunk) for chunk in chunks]


class Harness:
    def __init__(self, steps: Sequence[ScriptStep], inputs: Sequence[str]) -> None:
        self.adapter = ScriptedAdapter(steps)
        self.shown = StringIO()
        self.written: list[str] = []
        self.prompts: list[str] = []
        queue = iter(inputs)

        def reader(prompt: str) -> str:
            self.prompts.append(prompt)
            try:
                return next(queue)
            except StopIteration as error:
                raise EOFError from error

        console = Console(file=self.shown, width=100, no_color=True, force_terminal=False)
        self.session = ChatSession(
            self.adapter, request, reader=reader, emit=console.print, write=self.written.append
        )

    def run(self) -> None:
        asyncio.run(self.session.run())


def test_text_events_stream_incrementally_and_transcript_is_their_text() -> None:
    step = ScriptStep(result=result(text="ignored"), events=text_events("Hel", "lo"))
    harness = Harness([step], ["hi"])
    harness.run()
    assert harness.written == ["Hel", "lo", "\n"]
    assert harness.session.turns == [("hi", "Hello")]
    assert harness.session.transcript == "you: hi\n\nHello\n\n"
    assert harness.shown.getvalue() == ""


def test_non_text_events_are_not_shown() -> None:
    events = [AgentEvent("command", "ls"), *text_events("ok")]
    harness = Harness([ScriptStep(result=result(text="ok"), events=events)], ["hi"])
    harness.run()
    assert harness.written == ["ok", "\n"]


def test_final_text_prints_once_when_no_events_arrive() -> None:
    harness = Harness([ScriptStep(result=result(text="Only answer."))], ["hi"])
    harness.run()
    assert harness.shown.getvalue().count("Only answer.") == 1
    assert harness.written == []
    assert harness.session.turns == [("hi", "Only answer.")]


def test_without_writer_streamed_text_is_shown_once_at_the_end() -> None:
    shown = StringIO()
    console = Console(file=shown, width=100, force_terminal=False)
    adapter = ScriptedAdapter([ScriptStep(result=result(), events=text_events("a", "b"))])
    session = ChatSession(adapter, request, reader=lambda _: "", emit=console.print)
    assert asyncio.run(session.ask("q"))
    assert shown.getvalue() == "ab\n"


def test_adapter_failure_is_reported_and_the_session_continues() -> None:
    steps = [
        ScriptStep(result=result(ok=False, error="Provider unavailable")),
        ScriptStep(result=result(text="Recovered.")),
    ]
    harness = Harness(steps, ["one", "two"])
    harness.run()
    out = harness.shown.getvalue()
    assert "Follow-up failed: Provider unavailable" in out
    assert "Recovered." in out
    assert harness.session.turns == [("two", "Recovered.")]


def test_adapter_exception_is_reported_and_the_session_continues() -> None:
    class Failing(ScriptedAdapter):
        async def run(self, request, on_event=None):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

    harness = Harness([], ["one"])
    harness.session.adapter = Failing([])
    harness.run()
    assert "Follow-up failed: boom" in harness.shown.getvalue()


def test_trailing_backslash_continues_the_line() -> None:
    harness = Harness([ScriptStep(result=result(text="ok"))], ["first \\", "second \\", "third"])
    harness.run()
    assert harness.adapter.requests[0].prompt == "first \nsecond \nthird"
    assert harness.prompts == ["you> ", "... ", "... ", "you> "]


def test_help_lists_commands_and_unknown_command_is_refused() -> None:
    harness = Harness([], ["/help", "/bogus"])
    harness.run()
    out = harness.shown.getvalue()
    assert "/help" in out and "/save PATH" in out and "/quit" in out
    assert "Unknown command /bogus" in out
    assert harness.adapter.requests == []


def test_quit_and_end_of_input_end_the_session() -> None:
    harness = Harness([], ["/quit", "never read"])
    harness.run()
    assert harness.prompts == ["you> "]
    eof = Harness([], [])
    eof.run()
    assert eof.adapter.requests == []


def test_blank_lines_are_ignored() -> None:
    harness = Harness([], ["", "   "])
    harness.run()
    assert harness.adapter.requests == []


def test_save_writes_utf8_transcript_and_refuses_to_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "chat.txt"
    harness = Harness(
        [ScriptStep(result=result(), events=text_events("café ", "ok"))],
        ["question", f"/save {target}", f"/save {target}"],
    )
    harness.run()
    assert target.read_bytes() == "you: question\n\ncafé ok\n\n".encode()
    out = harness.shown.getvalue()
    assert "Saved transcript" in out
    assert "Refusing to overwrite existing file" in out
    assert target.read_text(encoding="utf-8").startswith("you: question")


def test_save_without_a_path_shows_usage() -> None:
    harness = Harness([], ["/save"])
    harness.run()
    assert "Usage: /save PATH" in harness.shown.getvalue()


def test_save_to_an_unwritable_path_reports_and_continues(tmp_path: Path) -> None:
    harness = Harness([], [f"/save {tmp_path / 'missing' / 'chat.txt'}"])
    harness.run()
    assert "Could not save transcript" in harness.shown.getvalue()


def test_live_markdown_on_a_terminal() -> None:
    shown = StringIO()
    console = Console(file=shown, width=60, force_terminal=True, color_system=None)
    step = ScriptStep(result=result(), events=text_events("# Title\n\n", "body"))
    adapter = ScriptedAdapter([step])
    session = ChatSession(adapter, request, reader=lambda _: "", console=console, live=True)
    assert asyncio.run(session.ask("q"))
    assert "Title" in shown.getvalue() and "body" in shown.getvalue()
    assert session.turns == [("q", "# Title\n\nbody")]


def test_extra_commands_help_and_turn_limit() -> None:
    harness = Harness(
        [ScriptStep(result=result(text="answer"))],
        [
            "/help",
            "/continue no",
            "question",
            "ignored",
            "/continue yes",
            "never read",
        ],
    )
    arguments: list[str] = []

    async def extra(argument: str) -> bool:
        arguments.append(argument)
        return argument == "yes"

    harness.session.commands = {"/continue": extra}
    harness.session.max_turns = 1
    harness.run()
    assert arguments == ["no", "yes"]
    assert len(harness.adapter.requests) == 1
    assert "/continue" in harness.shown.getvalue()
    assert "Turn limit reached" in harness.shown.getvalue()
    assert "Unknown command" not in harness.shown.getvalue()


def test_callbacks_and_unrecorded_exchange() -> None:
    harness = Harness(
        [
            ScriptStep(result=result(text="initial")),
            ScriptStep(result=result(text="answer")),
            ScriptStep(result=result(ok=False, error="Unavailable")),
        ],
        [],
    )
    results: list[AgentResult] = []
    turns: list[tuple[str, str]] = []
    harness.session.on_result = results.append
    harness.session.on_turn = lambda question, answer: turns.append((question, answer))
    assert asyncio.run(harness.session.ask("initial", record=False))
    assert harness.session.turns == [] and turns == []
    assert asyncio.run(harness.session.ask("question"))
    assert not asyncio.run(harness.session.ask("failed"))
    assert turns == [("question", "answer")]
    assert len(results) == 3
