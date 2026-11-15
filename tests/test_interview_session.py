"""Bounded scripted interviews without provider or GitHub access."""

import asyncio
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from functools import partial
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from fugue.agents import (
    AccessMode,
    AgentEvent,
    AgentRequest,
    AgentResult,
    ScriptedAdapter,
    ScriptStep,
)
from fugue.cli.choices import ChoiceItem, LineChooser
from fugue.config.schema import Limits
from fugue.interview.record import InterviewRecord
from fugue.interview.render import render_conversation, render_header
from fugue.interview.session import InterviewSession
from fugue.interview.store import InterviewStore
from fugue.models import AuthenticationMode, Role
from fugue.workflow import RunState


def result(text: str = "Intent?", **changes: Any) -> AgentResult:
    value = AgentResult(
        ok=True,
        final_text=text,
        structured_output=None,
        session_id=None,
        usage=None,
        provider="claude",
        authentication_mode=AuthenticationMode.SUBSCRIPTION_LOGIN,
        error=None,
        timed_out=False,
        cancelled=False,
        log_path=None,
        unsupported=None,
    )
    return replace(value, **changes)


def read(commands: Iterator[str]) -> Callable[[str], str]:
    def reader(prompt: str) -> str:
        try:
            return next(commands)
        except StopIteration as error:
            raise EOFError from error

    return reader


def session(
    tmp_path: Path, commands: list[str], *, maximum: int = 2
) -> tuple[InterviewSession, ScriptedAdapter, list[object]]:
    record = InterviewRecord(repo="owner/name", ticket=90)
    store = InterviewStore(tmp_path)
    analyst = ScriptedAdapter([ScriptStep(result=result()) for _ in range(maximum + 1)])
    messages: list[object] = []

    def request(role: Role, text: str) -> AgentRequest:
        return AgentRequest(role, text, tmp_path, AccessMode.READ_ONLY)

    interview = InterviewSession(
        record,
        store,
        analyst,
        request,
        limits=Limits(interview_max_turns=maximum),
        reader=read(iter(commands)),
        print_fn=messages.append,
    )
    return interview, analyst, messages


def test_human_saves_each_turn_and_proceeds(tmp_path: Path) -> None:
    interview, analyst, _ = session(tmp_path, [])
    commands = iter(["  exact text  ", "/proceed"])

    def reader(prompt: str) -> str:
        text = next(commands)
        if text == "/proceed":
            saved = interview.store.load("owner/name", 90)
            assert saved is not None
            assert saved.turns[1].text == "  exact text  "
            assert interview.store.path("owner/name", 90).stat().st_mode & 0o777 == 0o600
        return text

    interview.reader = reader
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert interview.record.status == "proceeded"
    assert interview.record.ended_by == "person"
    assert len(analyst.requests) == 2


@pytest.mark.parametrize("history", ["new", "opening", "reply", "interrupted", "no_analyst"])
def test_header_and_resume_precede_prompt_without_calling_analyst(
    tmp_path: Path, history: str
) -> None:
    interview, analyst, messages = session(tmp_path, [])
    interview.header = render_header(90, "Choose [directions]", "Keep **intent**.\nBoundaries.")
    if history not in {"new", "no_analyst"}:
        interview.record.add_turn("analyst", "Earlier question?")
    if history in {"reply", "interrupted", "no_analyst"}:
        interview.record.add_turn("person", "Earlier answer")
    if history == "reply":
        interview.record.add_turn("analyst", "Latest **question**?")
    original = interview.record.model_copy(deep=True)

    def reader(prompt: str) -> str:
        assert prompt == "you> "
        output = [str(message) for message in messages]
        assert output[0] == interview.header
        if history == "new":
            assert output[1:] == ["Intent?"]
        else:
            assert output[1] == f"Interview turns: {original.turn_count}"
            expected = (
                []
                if history == "no_analyst"
                else ["Latest **question**?" if history == "reply" else "Earlier question?"]
            )
            assert output[2:] == expected
            assert analyst.requests == []
            assert interview.record.turns == original.turns
        return "/quit"

    interview.reader = reader
    assert asyncio.run(interview.run()) == RunState.INTERRUPTED
    assert len(analyst.requests) == (1 if history == "new" else 0)


@pytest.mark.parametrize("mode", ["terminal", "piped", "no_color", "dumb", "narrow"])
def test_resume_uses_reply_rendering(tmp_path: Path, mode: str) -> None:
    shown = StringIO()
    console = Console(
        file=shown,
        width=59 if mode == "narrow" else 80,
        force_terminal=mode != "piped",
        no_color=mode == "no_color",
        _environ={"TERM": "dumb" if mode == "dumb" else "xterm"},
    )
    interview, analyst, _ = session(tmp_path, [])
    interview.header = render_header(90, "Choose [directions]", "Keep **intent**.")
    interview.record.add_turn("analyst", "Choose **small** steps with `literal` text.")
    interview.console = console
    interview.print = console.print
    interview.live = True

    def reader(prompt: str) -> str:
        output = shown.getvalue()
        assert "Ticket #90: Choose [directions]" in output
        assert "Summary:\nKeep **intent**." in output
        assert output.index("Ticket #90") < output.index("Interview turns: 0")
        assert output.index("Interview turns: 0") < output.index(
            "Choose", output.index("Interview turns")
        )
        if mode == "terminal":
            assert "**small**" not in output
            assert "`literal`" not in output
        else:
            assert interview.record.turns[0].text in output
            assert "\x1b" not in output
        assert not console._live_stack
        return "/quit"

    interview.reader = reader
    assert asyncio.run(interview.run()) == RunState.INTERRUPTED
    assert analyst.requests == []


def test_finished_interview_does_not_display_header_or_replay(tmp_path: Path) -> None:
    interview, analyst, messages = session(tmp_path, [])
    interview.header = "Ticket header"
    interview.record.add_turn("analyst", "Earlier question?")
    interview.record.status = "proceeded"
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert messages == []
    assert analyst.requests == []


@pytest.mark.parametrize("commands", [["/quit"], []])
def test_quit_eof_and_resume(tmp_path: Path, commands: list[str]) -> None:
    interview, analyst, _ = session(tmp_path, ["earlier", *commands])
    assert asyncio.run(interview.run()) == RunState.INTERRUPTED
    assert interview.record.status == "open"
    count = len(analyst.requests)
    interview.reader = read(iter(["/proceed"]))
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert len(analyst.requests) == count
    assert interview.record.turns[1].text == "earlier"


def test_limit_and_save_never_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "conversation.md"
    interview, analyst, messages = session(
        tmp_path,
        ["one", "ignored", f"/save {target}", f"/save {target}", "/proceed"],
        maximum=1,
    )
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert len(analyst.requests) == 2
    assert interview.record.turn_count == 1
    assert "person: one" in target.read_text()
    assert any("Turn limit" in str(message) for message in messages)
    assert any("Refusing to overwrite" in str(message) for message in messages)


@pytest.mark.parametrize("limited", [False, True])
def test_provider_failure_stops_and_preserves_record(tmp_path: Path, limited: bool) -> None:
    interview, _, _ = session(tmp_path, ["must not read"])
    interview.analyst.adapter = ScriptedAdapter(
        [ScriptStep(result=result(ok=False, provider_limited=limited, error="Unavailable"))]
    )
    expected = RunState.PROVIDER_LIMITED if limited else RunState.FAILED
    assert asyncio.run(interview.run()) == expected
    assert interview.record.status == "open"


def test_interrupt_keeps_person_turn(tmp_path: Path) -> None:
    interview, _, _ = session(tmp_path, ["keep me"])

    def interrupt(request: AgentRequest) -> None:
        raise KeyboardInterrupt

    interview.analyst.adapter = ScriptedAdapter(
        [
            ScriptStep(result=result()),
            ScriptStep(side_effect=interrupt),
        ]
    )
    assert asyncio.run(interview.run()) == RunState.INTERRUPTED
    assert interview.record.turns[-1].text == "keep me"


@pytest.mark.parametrize("ending", ["proceed", "needs_human", "limit"])
def test_automatic_endings_and_human_continuation(tmp_path: Path, ending: str) -> None:
    interview, analyst, messages = session(tmp_path, [], maximum=2)
    interview.header = "Ticket header"
    reply = {
        "reply": "Only the given scope",
        "proceed": ending == "proceed",
        "needs_human": ending == "needs_human",
        "reason": "Need consent",
    }
    standin = ScriptedAdapter(
        [ScriptStep(result=result(structured_output=reply, provider="codex")) for _ in range(2)]
    )
    from fugue.interview.session import _TimedAdapter

    interview.standin = _TimedAdapter(standin, 300)
    state = asyncio.run(interview.run(automatic=True))
    assert "Ticket header" not in [str(message) for message in messages]
    assert not any("Interview turns:" in str(message) for message in messages)
    assert len(standin.requests) <= 2
    assert len(analyst.requests) <= 3
    assert any(turn.speaker == "stand_in" for turn in interview.record.turns)
    if ending == "needs_human":
        assert state == RunState.BLOCKED
        assert "Need consent" in messages
        interview.reader = read(iter(["human answer", "/proceed"]))
        assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
        assert interview.record.mode == "human"
        assert interview.record.turns[-2].speaker == "person"
        assert any(turn.speaker == "stand_in" for turn in interview.record.turns)
    else:
        assert state == RunState.WAITING_FOR_SELECTION
        assert interview.record.ended_by == ("turn_limit" if ending == "limit" else "stand_in")


@pytest.mark.parametrize("automatic", [False, True])
def test_time_bounds_save_record_and_call_usage(tmp_path: Path, automatic: bool) -> None:
    interview, _, _ = session(tmp_path, [])
    limits = Limits(interview_call_timeout_seconds=1, interview_automatic_max_seconds=1)
    interview.limits = limits
    from fugue.interview.session import _TimedAdapter

    interview.analyst = _TimedAdapter(
        ScriptedAdapter([ScriptStep(delay=2)]),
        1,
        lambda result: interview._result(Role.ANALYST, result),
    )
    interview.standin = _TimedAdapter(ScriptedAdapter([]), 1)
    calls: list[Role] = []
    interview.on_result = lambda role, result: calls.append(role)
    state = asyncio.run(interview.run(automatic=automatic))
    assert state == (RunState.BLOCKED if automatic else RunState.FAILED)
    assert calls == [Role.ANALYST]
    assert interview.store.load("owner/name", 90) is not None
    assert interview.record.status == "open"


def test_adapter_exception_is_failed_without_further_input(tmp_path: Path) -> None:
    interview, _, _ = session(tmp_path, [])

    def fail(request: AgentRequest) -> None:
        raise RuntimeError("Offline")

    interview.analyst.adapter = ScriptedAdapter([ScriptStep(side_effect=fail)])
    assert asyncio.run(interview.run()) == RunState.FAILED
    assert interview.reason == "Offline"


@pytest.mark.parametrize("maximum", [1, 2])
def test_needs_human_at_last_turn_preserves_capacity_after_reload(
    tmp_path: Path,
    maximum: int,
) -> None:
    interview, _, _ = session(tmp_path, [], maximum=maximum)
    for _ in range(maximum - 1):
        interview.record.add_turn("stand_in", "Known audience details", max_turns=maximum)
        interview.record.add_turn("analyst", "Who is the intended audience?", max_turns=maximum)
    standin = ScriptedAdapter(
        [
            ScriptStep(
                result=result(
                    structured_output={
                        "reply": "Need the intended audience",
                        "proceed": False,
                        "needs_human": True,
                        "reason": "Audience unspecified",
                    }
                )
            )
        ]
    )
    from fugue.interview.session import _TimedAdapter

    interview.standin = _TimedAdapter(standin, 300)
    assert asyncio.run(interview.run(automatic=True)) == RunState.BLOCKED
    saved = interview.store.load("owner/name", 90)
    assert saved is not None and saved.status == "open"
    assert saved.turns[-1].speaker == "stand_in"
    assert saved.turns[-1].text == "Need the intended audience"
    resumed, analyst, messages = session(
        tmp_path,
        ["Internal developers", "ignored after limit", "/proceed"],
        maximum=maximum,
    )
    resumed.record = saved
    assert asyncio.run(resumed.run()) == RunState.WAITING_FOR_SELECTION
    assert [request.prompt for request in analyst.requests] == ["Internal developers"]
    final = interview.store.load("owner/name", 90)
    assert final is not None
    assert final.turn_count == maximum
    assert final.turns[-2].speaker == "person"
    assert final.turns[-2].text == "Internal developers"
    assert any("Turn limit reached" in str(message) for message in messages)


@pytest.mark.parametrize(
    ("text", "expected", "ready"),
    [
        ("Directions\n[INTERVIEW_READY]", "Directions", True),
        ("Directions\n[INTERVIEW_READY]\n", "Directions", True),
        ("[INTERVIEW_READY]", "", True),
        ("Directions\r\n[INTERVIEW_READY]\r\n", "Directions", True),
        ("Directions\n[INTERVIEW_READY]\n\n", "Directions\n[INTERVIEW_READY]\n\n", False),
        ("[INTERVIEW_READY]\nMore", "[INTERVIEW_READY]\nMore", False),
        ("Directions [INTERVIEW_READY]", "Directions [INTERVIEW_READY]", False),
        ("Directions\n[INTERVIEW_READY] extra", "Directions\n[INTERVIEW_READY] extra", False),
    ],
)
def test_ready_marker_only_on_final_line(text: str, expected: str, ready: bool) -> None:
    from fugue.interview.prompts import strip_ready

    assert strip_ready(text) == (expected, ready)


@pytest.mark.parametrize("interactive", [False, True])
def test_ready_confirmation_and_noninteractive_proceed(tmp_path: Path, interactive: bool) -> None:
    from io import StringIO

    from fugue.cli.choices import LineChooser
    from fugue.interview.prompts import READY_MARKER

    interview, _, messages = session(tmp_path, ["/proceed"])
    shown = StringIO()
    interview.chooser = LineChooser(stdin=StringIO("1\n"), stdout=shown)
    interview.interactive = interactive
    interview.analyst.adapter = ScriptedAdapter(
        [ScriptStep(result=result(f"Three directions.\n{READY_MARKER}"))]
    )
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert interview.record.ended_by == ("person_and_analyst" if interactive else "person")
    assert shown.getvalue().count("Start the analysis?") == int(interactive)
    assert interview.record.turns[0].text == "Three directions."
    assert all(READY_MARKER not in str(message) for message in messages)
    saved = interview.store.finished("owner/name", 90)
    assert saved is not None and saved.ended_by == interview.record.ended_by


@pytest.mark.parametrize("answer", ["2\n", "q\n"])
def test_decline_or_cancel_waits_for_another_signal(tmp_path: Path, answer: str) -> None:
    from io import StringIO

    from fugue.cli.choices import LineChooser
    from fugue.interview.prompts import READY_MARKER

    interview, _, _ = session(tmp_path, ["more detail", "done"])
    shown = StringIO()
    interview.chooser = LineChooser(stdin=StringIO(answer + "1\n"), stdout=shown)
    interview.interactive = True
    interview.analyst.adapter = ScriptedAdapter(
        [
            ScriptStep(result=result(f"Directions\n{READY_MARKER}")),
            ScriptStep(result=result("Another question?")),
            ScriptStep(result=result(f"Updated directions\n{READY_MARKER}")),
        ]
    )
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert shown.getvalue().count("Start the analysis?") == 2
    assert interview.record.turn_count == 2
    assert interview.record.ended_by == "person_and_analyst"


def test_automatic_ignores_analyst_ready_signal(tmp_path: Path) -> None:
    from io import StringIO

    from fugue.cli.choices import LineChooser
    from fugue.interview.prompts import READY_MARKER
    from fugue.interview.session import _TimedAdapter

    interview, _, _ = session(tmp_path, [])
    shown = StringIO()
    interview.chooser = LineChooser(stdin=StringIO("1\n"), stdout=shown)
    interview.interactive = True
    interview.analyst.adapter = ScriptedAdapter(
        [ScriptStep(result=result(f"Directions\n{READY_MARKER}"))]
    )
    interview.standin = _TimedAdapter(
        ScriptedAdapter(
            [
                ScriptStep(
                    result=result(
                        structured_output={
                            "reply": "Enough known",
                            "proceed": True,
                            "needs_human": False,
                            "reason": "",
                        }
                    )
                )
            ]
        ),
        300,
    )
    assert asyncio.run(interview.run(automatic=True)) == RunState.WAITING_FOR_SELECTION
    assert interview.record.ended_by == "stand_in"
    assert shown.getvalue() == ""
    assert interview.record.turns[0].text == "Directions"


def test_marker_in_middle_does_not_ask_for_confirmation(tmp_path: Path) -> None:
    from io import StringIO

    from fugue.agents import AgentEvent
    from fugue.cli.choices import LineChooser
    from fugue.interview.prompts import READY_MARKER

    interview, _, _ = session(tmp_path, ["/proceed"])
    shown = StringIO()
    interview.chooser = LineChooser(stdin=StringIO("1\n"), stdout=shown)
    interview.interactive = True
    answer = f"Directions\n{READY_MARKER}\nMore questions?"
    interview.analyst.adapter = ScriptedAdapter(
        [ScriptStep(result=result(answer), events=[AgentEvent("text", answer)])]
    )
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert interview.record.ended_by == "person"
    assert interview.record.turns[0].text == answer
    assert shown.getvalue() == ""


def test_confirmation_eof_keeps_interview_open_and_streamed_marker_is_removed(
    tmp_path: Path,
) -> None:
    from io import StringIO

    from fugue.agents import AgentEvent
    from fugue.cli.choices import LineChooser
    from fugue.interview.prompts import READY_MARKER

    interview, _, messages = session(tmp_path, ["/quit"])
    shown = StringIO()
    interview.chooser = LineChooser(stdin=StringIO(""), stdout=shown)
    interview.interactive = True
    interview.analyst.adapter = ScriptedAdapter(
        [
            ScriptStep(
                result=result(),
                events=[AgentEvent("text", "Directions\n"), AgentEvent("text", READY_MARKER)],
            )
        ]
    )
    assert asyncio.run(interview.run()) == RunState.INTERRUPTED
    assert interview.record.status == "open"
    assert interview.record.ended_by is None
    assert interview.record.turns[0].text == "Directions"
    assert all(READY_MARKER not in str(message) for message in messages)
    assert shown.getvalue().count("Start the analysis?") == 1


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("mode", ["terminal", "piped", "no_color", "dumb", "narrow"])
def test_markdown_display_preserves_turns_and_transcript(
    tmp_path: Path, streamed: bool, live: bool, mode: str
) -> None:
    shown = StringIO()
    console = Console(
        file=shown,
        width=59 if mode == "narrow" else 60,
        force_terminal=mode != "piped",
        no_color=mode == "no_color",
        _environ={"TERM": "dumb" if mode == "dumb" else "xterm"},
    )
    answer = "## Directions\n\nChoose **small** steps with `literal` text."
    chunks = [answer[:20], answer[20:]]
    analyst = ScriptedAdapter(
        [
            ScriptStep(
                result=result(answer),
                events=[AgentEvent("text", chunk) for chunk in chunks] if streamed else [],
            )
        ]
    )
    target = tmp_path / "conversation.md"
    commands = read(iter([f"/save {target}", "/proceed"]))

    def reader(prompt: str) -> str:
        assert not console._live_stack
        command = commands(prompt)
        if command == "/proceed":
            assert target.read_text(encoding="utf-8") == render_conversation(interview.record)
        return command

    interview = InterviewSession(
        InterviewRecord(repo="owner/name", ticket=90),
        InterviewStore(tmp_path),
        analyst,
        lambda role, text: AgentRequest(role, text, tmp_path, AccessMode.READ_ONLY),
        reader=reader,
        print_fn=console.print,
        console=console,
        live=live,
        write=partial(console.out, end=""),
    )
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    output = shown.getvalue()
    if mode == "terminal":
        assert "## Directions" not in output
        assert "**small**" not in output
        assert "`literal`" not in output
        assert "Directions" in output
    else:
        assert answer in output
        assert "\x1b" not in output
    assert interview.record.turns[0].text == answer
    saved = interview.store.load("owner/name", 90)
    assert saved is not None and saved.turns[0].text == answer
    assert answer in target.read_text(encoding="utf-8")


@pytest.mark.parametrize("failed", [False, True])
def test_live_reply_finishes_before_confirmation_or_failure(tmp_path: Path, failed: bool) -> None:
    from fugue.cli.choices import LineChooser
    from fugue.interview.prompts import READY_MARKER

    shown = StringIO()
    console = Console(file=shown, width=80, force_terminal=True, _environ={"TERM": "xterm"})
    answer = f"**Directions**\n{READY_MARKER}"
    analyst = ScriptedAdapter(
        [
            ScriptStep(
                result=result(answer, ok=not failed, error="Offline" if failed else None),
                events=[AgentEvent("text", answer)],
            )
        ]
    )

    class ReadyChooser(LineChooser):
        def confirm(self, message: str, default: bool = False) -> bool:
            assert not console._live_stack
            return super().confirm(message, default=default)

    interview = InterviewSession(
        InterviewRecord(repo="owner/name", ticket=90),
        InterviewStore(tmp_path),
        analyst,
        lambda role, text: AgentRequest(role, text, tmp_path, AccessMode.READ_ONLY),
        reader=read(iter([])),
        chooser=ReadyChooser(stdin=StringIO("1\n"), stdout=shown),
        interactive=True,
        print_fn=console.print,
        console=console,
        live=True,
    )
    assert asyncio.run(interview.run()) == (
        RunState.FAILED if failed else RunState.WAITING_FOR_SELECTION
    )
    assert not console._live_stack
    assert READY_MARKER not in shown.getvalue()
    if failed:
        assert "Follow-up failed: Offline" in shown.getvalue()
        assert interview.record.turns == []
        assert "Start the analysis?" not in shown.getvalue()
    else:
        assert interview.record.turns[0].text == "**Directions**"
        assert "Start the analysis?" in shown.getvalue()


class RequestChooser:
    def __init__(self, indexes: list[int] | None = None, *, confirmed: bool = False) -> None:
        self.indexes = indexes
        self.confirmed = confirmed
        self.calls: list[tuple[str, object]] = []

    def choose(
        self, message: str, choices: Sequence[str | ChoiceItem], default: int = 0
    ) -> int | None:
        self.calls.append((message, list(choices)))
        assert default == 1
        return None if self.indexes is None else self.indexes[0]

    def choose_many(
        self,
        message: str,
        items: Sequence[str | ChoiceItem],
        defaults: Sequence[int] = (),
        *,
        allow_empty: bool = False,
    ) -> list[int] | None:
        self.calls.append((message, list(items)))
        return self.indexes

    def confirm(self, message: str, default: bool = False) -> bool:
        assert default is False
        self.calls.append((message, default))
        return self.confirmed


def typed_reply(kind: str, *, allow_custom: bool = False) -> dict[str, object]:
    request: dict[str, object] = {"kind": kind, "question": "Pick a direction"}
    if kind in {"single_choice", "multiple_choice"}:
        request.update(
            options=[
                {"label": "Small", "description": "Less work"},
                {"label": "Large", "description": "More coverage"},
            ],
            recommended=1,
            allow_custom=allow_custom,
        )
    return {"message": "Consider **these** directions.", "request": request, "ready": False}


@pytest.mark.parametrize(
    ("kind", "indexes", "confirmed", "expected"),
    [
        ("single_choice", [1], False, "Large"),
        ("multiple_choice", [0, 1], False, "Small, Large"),
        ("confirmation", [0], True, "Yes"),
        ("confirmation", [1], False, "No"),
        ("free_text", None, False, "My direction"),
        ("information", None, False, None),
    ],
)
def test_typed_requests_record_answers(
    tmp_path: Path, kind: str, indexes: list[int] | None, confirmed: bool, expected: str | None
) -> None:
    commands = ["My direction", "/proceed"] if kind == "free_text" else ["/proceed"]
    interview, _, messages = session(tmp_path, commands)
    chooser = RequestChooser(indexes, confirmed=confirmed)
    interview.chooser = chooser
    set_analyst_reply(interview, ScriptStep(result=result(structured_output=typed_reply(kind))))
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert interview.record.turns[0].text == "Consider **these** directions."
    assert [turn.text for turn in interview.record.turns if turn.speaker == "person"] == (
        [] if expected is None else [expected]
    )
    if kind in {"information", "free_text"}:
        assert chooser.calls == []
        assert "Pick a direction" in [str(message) for message in messages]
    elif kind in {"single_choice", "multiple_choice"}:
        assert chooser.calls[0][1] == [
            ChoiceItem("Small", "Less work"),
            ChoiceItem("Large", "More coverage", recommended=True),
        ]
    saved = interview.store.load("owner/name", 90)
    assert saved is not None and saved.turns == interview.record.turns


@pytest.mark.parametrize("kind", ["single_choice", "multiple_choice"])
def test_custom_choice_uses_chat_line(tmp_path: Path, kind: str) -> None:
    interview, _, _ = session(tmp_path, ["Custom direction", "/proceed"])
    chooser = RequestChooser([2] if kind == "single_choice" else [0, 2])
    interview.chooser = chooser
    set_analyst_reply(
        interview, ScriptStep(result=result(structured_output=typed_reply(kind, allow_custom=True)))
    )
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert interview.record.turns[1].text == (
        "Custom direction" if kind == "single_choice" else "Small, Custom direction"
    )
    assert chooser.calls[0][1] == [
        ChoiceItem("Small", "Less work"),
        ChoiceItem("Large", "More coverage", recommended=True),
        ChoiceItem("Other"),
    ]


@pytest.mark.parametrize("structured", [None, {}, {"message": ""}, {"request": {"kind": "bad"}}])
def test_invalid_typed_reply_falls_back(tmp_path: Path, structured: object) -> None:
    interview, _, messages = session(tmp_path, ["/proceed"])
    set_analyst_reply(
        interview,
        ScriptStep(
            result=result("Plain question?\n[INTERVIEW_READY]", structured_output=structured)
        ),
    )
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert interview.record.turns[0].text == "Plain question?"
    assert "Plain question?" in [str(message) for message in messages]


@pytest.mark.parametrize("kind", ["single_choice", "multiple_choice", "confirmation"])
def test_cancelled_request_records_no_answer(tmp_path: Path, kind: str) -> None:
    interview, _, _ = session(tmp_path, [])
    interview.chooser = RequestChooser()
    set_analyst_reply(interview, ScriptStep(result=result(structured_output=typed_reply(kind))))
    assert asyncio.run(interview.run()) == RunState.INTERRUPTED
    assert [turn.speaker for turn in interview.record.turns] == ["analyst"]
    saved = interview.store.load("owner/name", 90)
    assert saved is not None
    interview.record = saved
    interview.reader = read(iter(["Resumed answer", "/proceed"]))
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert interview.record.turns[1].text == "Resumed answer"


@pytest.mark.parametrize(
    ("kind", "line", "expected"),
    [("single_choice", "1\n", "Small"), ("multiple_choice", "1,2\n", "Small, Large")],
)
def test_nonterminal_typed_request(tmp_path: Path, kind: str, line: str, expected: str) -> None:
    interview, _, _ = session(tmp_path, ["/proceed"])
    shown = StringIO()
    interview.chooser = LineChooser(stdin=StringIO(line), stdout=shown)
    interview.interactive = False
    set_analyst_reply(interview, ScriptStep(result=result(structured_output=typed_reply(kind))))
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert "1. Small" in shown.getvalue()
    assert "2. Large [recommended]" in shown.getvalue()
    assert interview.record.turns[1].text == expected


def test_typed_ready_finishes_live_before_confirmation(tmp_path: Path) -> None:
    interview, _, _ = session(tmp_path, [])
    shown = StringIO()
    console = Console(file=shown, force_terminal=True, width=80, _environ={"TERM": "xterm"})

    class ReadyChooser(RequestChooser):
        def confirm(self, message: str, default: bool = False) -> bool:
            assert not console._live_stack
            return super().confirm(message, default)

    interview.console = console
    interview.print = console.print
    interview.live = True
    interview.interactive = True
    chooser = ReadyChooser(confirmed=True)
    interview.chooser = chooser
    set_analyst_reply(
        interview,
        ScriptStep(result=result(structured_output={"message": "**Ready**", "ready": True})),
    )
    assert asyncio.run(interview.run()) == RunState.WAITING_FOR_SELECTION
    assert interview.record.ended_by == "person_and_analyst"
    assert chooser.calls == [("Start the analysis?", False)]
    assert "**Ready**" not in shown.getvalue()


@pytest.mark.parametrize("kind", ["single_choice", "information"])
def test_automatic_typed_reply_never_prompts_or_shows_panel(tmp_path: Path, kind: str) -> None:
    from fugue.interview.session import _TimedAdapter

    interview, _, _ = session(tmp_path, [])
    shown = StringIO()
    console = Console(file=shown, force_terminal=True, width=80, _environ={"TERM": "xterm"})
    interview.console = console
    interview.print = console.print
    interview.live = True
    chooser = RequestChooser([1])
    interview.chooser = chooser
    set_analyst_reply(interview, ScriptStep(result=result(structured_output=typed_reply(kind))))

    def standin_call(request: AgentRequest) -> None:
        assert not console._live_stack

    interview.standin = _TimedAdapter(
        ScriptedAdapter(
            [
                ScriptStep(
                    result=result(
                        structured_output={
                            "reply": "Enough known",
                            "proceed": True,
                            "needs_human": False,
                            "reason": "",
                        }
                    ),
                    side_effect=standin_call,
                )
            ]
        ),
        300,
    )
    assert asyncio.run(interview.run(automatic=True)) == RunState.WAITING_FOR_SELECTION
    assert chooser.calls == []
    assert "Pick a direction" not in shown.getvalue()
    assert "Information" not in shown.getvalue()


def set_analyst_reply(interview: InterviewSession, step: ScriptStep) -> None:
    interview.analyst.adapter = ScriptedAdapter([step, ScriptStep(result=result())])


@pytest.mark.parametrize("kind", ["single_choice", "multiple_choice", "confirmation"])
@pytest.mark.parametrize("keys", ["\x1b", "\x03\r"])
def test_terminal_request_cancellation_keeps_record(tmp_path: Path, kind: str, keys: str) -> None:
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from fugue.cli.choices import PromptChooser

    interview, _, _ = session(tmp_path, [])
    shown = StringIO()
    console = Console(file=shown, width=80, force_terminal=True, _environ={"TERM": "xterm"})
    interview.console = console
    interview.live = True
    set_analyst_reply(interview, ScriptStep(result=result(structured_output=typed_reply(kind))))
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        interview.chooser = PromptChooser(input=pipe, output=DummyOutput(), stdout=shown)
        assert asyncio.run(interview.run()) == RunState.INTERRUPTED
    assert not console._live_stack
    assert [turn.speaker for turn in interview.record.turns] == ["analyst"]
    assert interview.record.status == "open"


@pytest.mark.parametrize("kind", ["single_choice", "multiple_choice", "confirmation"])
def test_request_eof_does_not_record_answer(tmp_path: Path, kind: str) -> None:
    interview, _, _ = session(tmp_path, [])
    interview.chooser = LineChooser(stdin=StringIO(""), stdout=StringIO())
    set_analyst_reply(interview, ScriptStep(result=result(structured_output=typed_reply(kind))))
    assert asyncio.run(interview.run()) == RunState.INTERRUPTED
    assert interview.record.turn_count == 0
