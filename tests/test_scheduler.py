"""Concurrent runs use local fake engines and never call external services."""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from fugue.engine.locks import LockMetadata, TicketLock, TicketLocked
from fugue.engine.pipeline import EngineDeps, RunOutcome
from fugue.engine.scheduler import run_many
from fugue.models import IssueType, Priority, Size, Status, Ticket
from fugue.workflow import RunState


def ticket(number: int, *, blocked_by: list[int] | None = None) -> Ticket:
    return Ticket(
        number=number,
        title=f"Ticket {number}",
        issue_type=IssueType.FEATURE,
        size=Size.S,
        status=Status.TO_DO,
        priority=Priority.HIGH,
        blocked_by=blocked_by or [],
    )


class FakeEngine:
    def __init__(self, state_dir: Path, action: Callable[[int], Awaitable[RunOutcome]]) -> None:
        self.deps = cast(
            EngineDeps,
            SimpleNamespace(state_dir=state_dir, github=SimpleNamespace(repo="org/repo")),
        )
        self.action = action

    async def run_to_pr(self, ticket_number: int) -> RunOutcome:
        return await self.action(ticket_number)


async def success(number: int) -> RunOutcome:
    return RunOutcome(number, RunState.WAITING_FOR_CI)


@pytest.mark.parametrize("limit", [1, 2, 5])
def test_limit_respected_and_results_keep_input_order(tmp_path: Path, limit: int) -> None:
    async def scenario() -> None:
        release = asyncio.Event()
        filled = asyncio.Event()
        active = 0
        maximum = 0
        created = 0

        async def action(number: int) -> RunOutcome:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == min(limit, 4):
                filled.set()
            try:
                await release.wait()
                return await success(number)
            finally:
                active -= 1

        def factory() -> FakeEngine:
            nonlocal created
            created += 1
            return FakeEngine(tmp_path, action)

        task = asyncio.create_task(run_many([ticket(n) for n in range(1, 5)], factory, limit))
        await asyncio.wait_for(filled.wait(), 2)
        assert created == min(limit, 4)
        assert active == min(limit, 4)
        release.set()
        results = await asyncio.wait_for(task, 2)
        assert results == [await success(n) for n in range(1, 5)]
        assert maximum == min(limit, 4)
        assert active == 0
        assert created == 4

    asyncio.run(scenario())


def test_blocked_ticket_skipped_even_when_blocker_is_in_batch(tmp_path: Path) -> None:
    calls: list[int] = []

    async def action(number: int) -> RunOutcome:
        calls.append(number)
        return await success(number)

    results = asyncio.run(
        run_many([ticket(1, blocked_by=[2]), ticket(2)], lambda: FakeEngine(tmp_path, action), 2)
    )
    assert results == [None, RunOutcome(2, RunState.WAITING_FOR_CI)]
    assert calls == [2]


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_limit_refused_before_factory(limit: int) -> None:
    def factory() -> FakeEngine:
        pytest.fail("Invalid input must not create an engine")

    with pytest.raises(ValueError, match="limit must be positive"):
        asyncio.run(run_many([ticket(1)], factory, limit))


def test_duplicate_ticket_refused_before_any_run() -> None:
    def factory() -> FakeEngine:
        pytest.fail("Duplicate tickets must not create an engine")

    with pytest.raises(ValueError, match="same ticket"):
        asyncio.run(run_many([ticket(1), ticket(1)], factory, 1))


def test_empty_batch_does_not_create_engine() -> None:
    def factory() -> FakeEngine:
        pytest.fail("Empty batch must not create an engine")

    assert asyncio.run(run_many([], factory, 1)) == []


@pytest.mark.parametrize("factory_failure", [False, True])
def test_failure_does_not_cancel_other_runs(tmp_path: Path, factory_failure: bool) -> None:
    error = RuntimeError("one run failed")
    created = 0
    finished: list[int] = []

    async def action(number: int) -> RunOutcome:
        await asyncio.sleep(0)
        if number == 1 and not factory_failure:
            raise error
        finished.append(number)
        return await success(number)

    def factory() -> FakeEngine:
        nonlocal created
        created += 1
        if factory_failure and created == 1:
            raise error
        return FakeEngine(tmp_path, action)

    results = asyncio.run(run_many([ticket(n) for n in range(1, 4)], factory, 2))
    assert results == [
        error,
        RunOutcome(2, RunState.WAITING_FOR_CI),
        RunOutcome(3, RunState.WAITING_FOR_CI),
    ]
    assert finished == [2, 3]
    # Failure released the lock, even when the engine had already started.
    with TicketLock(tmp_path, "org/repo", 1):
        pass


@pytest.mark.parametrize("child_cancellation", [False, True])
def test_cancellation_propagates_and_waits_for_cleanup(
    tmp_path: Path, child_cancellation: bool
) -> None:
    async def scenario() -> None:
        filled = asyncio.Event()
        cancel_child = asyncio.Event()
        started: list[int] = []
        cleaned: list[int] = []
        never = asyncio.Event()

        async def action(number: int) -> RunOutcome:
            started.append(number)
            if len(started) == 2:
                filled.set()
            try:
                if number == 1 and child_cancellation:
                    await cancel_child.wait()
                    raise asyncio.CancelledError
                await never.wait()
                return await success(number)
            finally:
                await asyncio.sleep(0)
                cleaned.append(number)

        task = asyncio.create_task(
            run_many([ticket(n) for n in range(1, 5)], lambda: FakeEngine(tmp_path, action), 2)
        )
        await asyncio.wait_for(filled.wait(), 2)
        if child_cancellation:
            cancel_child.set()
        else:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert set(started) == set(cleaned)
        assert {1, 2}.issubset(cleaned)
        if not child_cancellation:
            assert started == [1, 2]
        for number in started:
            with TicketLock(tmp_path, "org/repo", number):
                pass

    asyncio.run(scenario())


def test_separate_batches_refuse_same_active_ticket(tmp_path: Path) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def action(number: int) -> RunOutcome:
            started.set()
            await release.wait()
            return await success(number)

        def factory() -> FakeEngine:
            return FakeEngine(tmp_path, action)

        first = asyncio.create_task(run_many([ticket(1)], factory, 1))
        await asyncio.wait_for(started.wait(), 2)
        second = await run_many([ticket(1), ticket(2, blocked_by=[1])], factory, 2)
        assert isinstance(second[0], TicketLocked)
        assert second[1] is None
        release.set()
        assert await first == [await success(1)]
        assert await run_many([ticket(1)], factory, 1) == [await success(1)]

    asyncio.run(scenario())


def test_lock_metadata_identity_and_exception_release(tmp_path: Path) -> None:
    lock = TicketLock(tmp_path, "Org/Repo", 1)
    with pytest.raises(RuntimeError, match="run failed"), lock:
        data = json.loads(lock.path.read_text())
        assert data["pid"] == os.getpid()
        metadata = LockMetadata(data["pid"], datetime.fromisoformat(data["created_at"]))
        assert 0 <= metadata.age_seconds < 5
        with (
            pytest.raises(TicketLocked, match=str(os.getpid())),
            TicketLock(tmp_path, " org/repo ", 1),
        ):
            pytest.fail("Duplicate lock acquired")
        with TicketLock(tmp_path, "org/another", 1), TicketLock(tmp_path, "org/repo", 2):
            pass
        raise RuntimeError("run failed")
    with TicketLock(tmp_path, "org/repo", 1):
        pass
    old = LockMetadata(1, datetime.now(UTC) - timedelta(seconds=60))
    assert old.age_seconds >= 60


def test_process_exit_releases_lock_without_context_exit(tmp_path: Path) -> None:
    script = """
import os
import sys
from pathlib import Path
from fugue.engine.locks import TicketLock
lock = TicketLock(Path(sys.argv[1]), 'org/repo', 1)
lock.__enter__()
os._exit(0)
"""
    lock = TicketLock(tmp_path, "org/repo", 1)
    with lock:
        refused = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert refused.returncode == 1
        assert "TicketLocked" in refused.stderr
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=False)
    assert result.returncode == 0
    assert json.loads(lock.path.read_text())["pid"] != os.getpid()
    with lock:
        assert json.loads(lock.path.read_text())["pid"] == os.getpid()
