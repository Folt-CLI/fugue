"""Review and approval CLI flows use only fake engines and a local run store."""

import asyncio
import getpass
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from typer.testing import CliRunner

from fugue.cli import app as cli
from fugue.engine import Engine, EngineDeps, RunOutcome
from fugue.engine.review import Decision, Finding
from fugue.store import Store
from fugue.store.records import Approval
from fugue.workflow import RunState


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.outcome = RunOutcome(7, RunState.WAITING_FOR_DECISION)
        self.error: BaseException | None = None
        self.closed = False
        self.deps = SimpleNamespace()

    async def call(self, name: str, value: object) -> RunOutcome:
        self.calls.append((name, value))
        if self.error is not None:
            raise self.error
        return self.outcome

    async def run_to_green(self, ticket: int) -> RunOutcome:
        return await self.call("green", ticket)

    async def run_review(self, run_id: int) -> RunOutcome:
        return await self.call("review", run_id)

    async def decide(self, run_id: int, decisions: Mapping[int, Decision]) -> RunOutcome:
        return await self.call("decide", (run_id, decisions))

    async def request_approval(self, run_id: int) -> RunOutcome:
        return await self.call("request-approval", run_id)

    def approve(self, run_id: int, decided_by: str, reason: str) -> Approval:
        # The real synchronous API starts its own event loop.
        asyncio.run(asyncio.sleep(0))
        self.calls.append(("approve", (run_id, decided_by, reason)))
        if self.error is not None:
            raise self.error
        return Approval(
            1,
            run_id,
            "merge",
            "12",
            "abc123",
            "approved",
            decided_by,
            reason,
            datetime.now(UTC),
            None,
        )

    async def merge_and_close(self, run_id: int) -> RunOutcome:
        return await self.call("merge", run_id)

    async def resume(self, run_id: int) -> RunOutcome:
        return await self.call("resume", run_id)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    fake = FakeEngine()
    monkeypatch.setattr(cli, "engine_factory", lambda *args: fake)
    monkeypatch.setattr(getpass, "getuser", lambda: "os-user")
    return fake


def invoke(tmp_path: Path, *args: str) -> tuple[int, str]:
    result = CliRunner().invoke(cli.app, [*args, "--repo-root", str(tmp_path)])
    return result.exit_code, result.output


@pytest.mark.parametrize("command", ["review", "request-approval", "merge", "resume"])
@pytest.mark.parametrize(
    "state,code",
    [
        (RunState.COMPLETED, 0),
        (RunState.BLOCKED, 3),
        (RunState.FAILED, 4),
        (RunState.PROVIDER_LIMITED, 5),
        (RunState.INTERRUPTED, 6),
    ],
)
def test_commands_dispatch_and_exit(
    tmp_path: Path, engine: FakeEngine, command: str, state: RunState, code: int
) -> None:
    engine.outcome = RunOutcome(7, state, problems=("detail",))
    actual, output = invoke(tmp_path, command, "7")
    assert actual == code
    assert engine.calls == [(command, 7)]
    assert "detail" in output
    assert engine.closed


def test_run_to_green(tmp_path: Path, engine: FakeEngine) -> None:
    code, _ = invoke(tmp_path, "run", "41", "--to-green")
    assert code == 0
    assert engine.calls == [("green", 41)]
    assert engine.closed


def test_findings_and_notes(tmp_path: Path, engine: FakeEngine) -> None:
    engine.outcome = RunOutcome(
        7,
        RunState.WAITING_FOR_DECISION,
        findings=(
            Finding(1, "High", "Wrong result", "src/example.py", 8, 9, None, True),
            Finding(2, "Low", "Suggestion", None, None, None, None, False),
        ),
        notes=("Review posted as COMMENT",),
    )
    code, output = invoke(tmp_path, "review", "7")
    assert code == 0
    for text in [
        "Blocking",
        "Non-blocking",
        "High",
        "src/example.py:8-9",
        "Wrong result",
        "Suggestion",
        "n/a",
        "Review posted as COMMENT",
        "next: fugue decide 7",
    ]:
        assert text in output


def test_decisions(tmp_path: Path, engine: FakeEngine) -> None:
    code, _ = invoke(
        tmp_path,
        "decide",
        "7",
        "--accept",
        "1",
        "--accept",
        "3",
        "--reject",
        "2:reason text: with colon",
    )
    assert code == 0
    assert engine.calls == [
        (
            "decide",
            (
                7,
                {
                    1: Decision(True),
                    3: Decision(True),
                    2: Decision(False, "reason text: with colon"),
                },
            ),
        )
    ]
    assert engine.closed


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--reject", "2"],
        ["--reject", "2:  "],
        ["--reject", "x:reason"],
        ["--accept", "0"],
        ["--accept", "-1"],
        ["--accept", "1", "--reject", "1:reason"],
        ["--accept", "1", "--accept", "1"],
        ["--reject", "2:a", "--reject", "2:b"],
        ["--accept", "abc"],
    ],
)
def test_invalid_decisions(tmp_path: Path, engine: FakeEngine, args: list[str]) -> None:
    code, _ = invoke(tmp_path, "decide", "7", *args)
    assert code == 2
    assert engine.calls == []


@pytest.mark.parametrize("by", [None, "claude-campaign"])
def test_approve(tmp_path: Path, engine: FakeEngine, by: str | None) -> None:
    args = [] if by is None else ["--by", by]
    code, output = invoke(tmp_path, "approve", "7", "--reason", "Ready to merge", *args)
    assert code == 0
    assert engine.calls == [("approve", (7, by or "os-user", "Ready to merge"))]
    for text in ["abc123", "Ready to merge", by or "os-user", "exact head SHA", "fugue merge 7"]:
        assert text in output
    assert engine.closed


@pytest.mark.parametrize("args", [[], ["--reason", " "], ["--reason", "ok", "--by", " "]])
def test_invalid_approval(tmp_path: Path, engine: FakeEngine, args: list[str]) -> None:
    assert invoke(tmp_path, "approve", "7", *args)[0] == 2
    assert engine.calls == []


@pytest.mark.parametrize(
    "command", ["review", "decide", "request-approval", "approve", "merge", "resume"]
)
@pytest.mark.parametrize(
    "error,code",
    [
        (ValueError("invalid input"), 2),
        (RuntimeError("failure"), 4),
        (asyncio.CancelledError(), 6),
        (KeyboardInterrupt(), 6),
    ],
)
def test_failures_close_engine(
    tmp_path: Path, engine: FakeEngine, command: str, error: BaseException, code: int
) -> None:
    engine.error = error
    args = ["--accept", "1"] if command == "decide" else []
    if command == "approve":
        args = ["--reason", "ready"]
    actual, output = invoke(tmp_path, command, "7", *args)
    assert actual == code
    assert "error:" in output
    assert engine.closed


@pytest.mark.parametrize(
    "progress,expected",
    [
        ("none", "green"),
        ("review", "review"),
        ("decision", "decide"),
        ("request", "request-approval"),
        ("approval", "merge"),
        ("merge", "merge"),
        ("completed", None),
    ],
)
def test_resume_durable_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, progress: str, expected: str | None
) -> None:
    fake = FakeEngine()
    monkeypatch.setattr(cli, "_ResumeEngine", lambda deps, run_id: fake)
    with Store(tmp_path / "store.db") as store:
        run = store.create_run(41, "owner/repo")
        if progress == "completed":
            store.set_run_state(run.id, RunState.COMPLETED)
        if progress == "review":
            store.begin_attempt(run.id, "review")
        if progress == "decision":
            store.begin_operation(
                run.id,
                "decision",
                "round-1",
                {
                    "decisions": {"1": {"accept": False, "reason": "saved reason"}},
                },
            )
        if progress == "request":
            store.begin_operation(run.id, "merge_request", "abc123", {"head": "abc123"})
        if progress == "approval":
            store.record_approval(run.id, "merge", "12", "abc123", "approved", "user", "ready")
        if progress == "merge":
            store.begin_operation(run.id, "merge", "abc123", {"head": "abc123"})
        fake.deps = SimpleNamespace(store=store)
        wrapper = cli._StoreClosingEngine(cast(Engine, fake))
        asyncio.run(wrapper.resume(run.id))
        assert [name for name, _ in fake.calls] == ([] if expected is None else [expected])
        if progress == "decision":
            assert fake.calls[0][1] == (run.id, {1: Decision(False, "saved reason")})


def test_resume_keeps_requested_run_when_ticket_has_newer_run(tmp_path: Path) -> None:
    with Store(tmp_path / "store.db") as store:
        requested = store.create_run(41, "owner/repo")
        store.set_run_state(requested.id, RunState.FAILED)
        store.create_run(41, "owner/repo")
        engine = cli._ResumeEngine(cast(EngineDeps, SimpleNamespace(store=store)), requested.id)
        assert engine._find_or_create_run(41).id == requested.id
        with pytest.raises(ValueError, match="does not match"):
            engine._find_or_create_run(42)
