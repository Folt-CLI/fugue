"""Bounded scripted interviews without provider or GitHub access."""

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fugue.agents import AccessMode, AgentRequest, AgentResult, ScriptedAdapter, ScriptStep
from fugue.config.schema import Limits
from fugue.interview.record import InterviewRecord
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
