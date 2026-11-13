"""Concurrent runs use local fake engines and never call external services."""

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from fugue.engine.locks import (
    LockMetadata,
    RepoLock,
    TicketLock,
    TicketLocked,
    pid_alive,
    read_metadata,
)
from fugue.engine.pipeline import Engine, EngineDeps, Identity, RunOutcome
from fugue.engine.scheduler import run_many
from fugue.models import IssueType, Priority, Size, Status, Ticket
from fugue.store import Store
from fugue.workflow import RunState, Stage


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
            SimpleNamespace(
                state_dir=state_dir, repo_root=state_dir, github=SimpleNamespace(repo="org/repo")
            ),
        )
        self.action = action

    async def run_to_pr(self, ticket_number: int) -> RunOutcome:
        return await self.action(ticket_number)

    async def run_to_green(self, ticket_number: int) -> RunOutcome:
        outcome = await self.action(ticket_number)
        return replace(outcome, state=RunState.COMPLETED)


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

        def factory(_ticket: Ticket) -> FakeEngine:
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
        run_many([ticket(1, blocked_by=[2]), ticket(2)], lambda _t: FakeEngine(tmp_path, action), 2)
    )
    assert results == [None, RunOutcome(2, RunState.WAITING_FOR_CI)]
    assert calls == [2]


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_limit_refused_before_factory(limit: int) -> None:
    def factory(_ticket: Ticket) -> FakeEngine:
        pytest.fail("Invalid input must not create an engine")

    with pytest.raises(ValueError, match="limit must be positive"):
        asyncio.run(run_many([ticket(1)], factory, limit))


def test_duplicate_ticket_refused_before_any_run() -> None:
    def factory(_ticket: Ticket) -> FakeEngine:
        pytest.fail("Duplicate tickets must not create an engine")

    with pytest.raises(ValueError, match="same ticket"):
        asyncio.run(run_many([ticket(1), ticket(1)], factory, 1))


def test_empty_batch_does_not_create_engine() -> None:
    def factory(_ticket: Ticket) -> FakeEngine:
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

    def factory(_ticket: Ticket) -> FakeEngine:
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
            run_many([ticket(n) for n in range(1, 5)], lambda _t: FakeEngine(tmp_path, action), 2)
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

        def factory(_ticket: Ticket) -> FakeEngine:
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


def test_prepare_mutations_are_serialised_while_agents_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        runs_started = asyncio.Event()
        agents_started = asyncio.Event()
        mutation_calls: list[str] = []
        preparing = 0
        maximum_preparing = 0
        agents = 0
        maximum_agents = 0
        started = 0
        created = 0

        class FakeGit:
            def __init__(self, repo: Path) -> None:
                assert repo.resolve() == tmp_path.resolve()

            async def fetch(self) -> None:
                nonlocal preparing, maximum_preparing
                mutation_calls.append("enter")
                preparing += 1
                maximum_preparing = max(maximum_preparing, preparing)
                # Keep the first preparation open until the other run starts.
                await runs_started.wait()

            async def fast_forward(self, branch: str) -> None:
                assert branch == "develop"
                mutation_calls.append("fast_forward")

            async def create_worktree(self, path: Path, branch: str, base: str) -> None:
                nonlocal preparing
                mutation_calls.append("worktree_add")
                preparing -= 1
                mutation_calls.append("exit")

        class PreparingEngine(Engine):
            async def run_to_pr(self, ticket_number: int) -> RunOutcome:
                nonlocal started
                run = self.deps.store.create_run(ticket_number, self.deps.github.repo)
                identity = Identity(f"ticket-{ticket_number}", tmp_path / str(ticket_number))
                started += 1
                if started == 2:
                    runs_started.set()
                await self._stage(
                    run.id,
                    Stage.PREPARE,
                    lambda: self._prepare(
                        run.id,
                        ticket(ticket_number).model_copy(
                            update={"status": Status.IN_IMPLEMENTATION}
                        ),
                        "scope",
                        identity,
                    ),
                )
                await self._agent(run.id, identity, "prompt", "scope")
                return await success(ticket_number)

            async def _agent(
                self, run_id: int, identity: Identity, prompt: str, scope: str
            ) -> None:
                nonlocal agents, maximum_agents
                agents += 1
                maximum_agents = max(maximum_agents, agents)
                if agents == 2:
                    agents_started.set()
                try:
                    await agents_started.wait()
                finally:
                    agents -= 1

        monkeypatch.setattr("fugue.engine.pipeline.Git", FakeGit)
        with Store(":memory:") as store:

            def factory(_ticket: Ticket) -> PreparingEngine:
                nonlocal created
                created += 1
                # Lexically different paths to the same primary repository.
                repo_root = tmp_path if created == 1 else tmp_path / "unused" / ".."
                deps = cast(
                    EngineDeps,
                    SimpleNamespace(
                        store=store,
                        repo_root=repo_root,
                        state_dir=tmp_path,
                        github=SimpleNamespace(repo="org/repo"),
                        base_branch="develop",
                        progress=None,
                        repo_lock=None,
                    ),
                )
                return PreparingEngine(deps)

            results = await asyncio.wait_for(run_many([ticket(1), ticket(2)], factory, 2), 2)
        assert results == [await success(1), await success(2)]
        assert mutation_calls == ["enter", "fast_forward", "worktree_add", "exit"] * 2
        assert maximum_preparing == 1
        assert maximum_agents == 2
        assert preparing == agents == 0

    asyncio.run(scenario())


def test_until_green_runs_to_green_and_on_complete_sees_each_result(tmp_path: Path) -> None:
    seen: list[tuple[int, object]] = []

    results = asyncio.run(
        run_many(
            [ticket(1), ticket(2, blocked_by=[9])],
            lambda _t: FakeEngine(tmp_path, success),
            2,
            until_green=True,
            on_complete=lambda t, result: seen.append((t.number, result)),
        )
    )
    assert results == [RunOutcome(1, RunState.COMPLETED), None]
    assert sorted(seen, key=lambda item: item[0]) == [(1, results[0]), (2, None)]


def test_factory_receives_the_ticket_and_errors_reach_on_complete(tmp_path: Path) -> None:
    received: list[int] = []
    completed: list[object] = []

    def factory(item: Ticket) -> FakeEngine:
        received.append(item.number)
        raise RuntimeError("no engine")

    results = asyncio.run(
        run_many([ticket(3)], factory, 1, on_complete=lambda _t, result: completed.append(result))
    )
    assert received == [3]
    assert isinstance(results[0], RuntimeError)
    assert completed == results


def test_ticket_lock_records_recovery_and_clean_release_empties_file(tmp_path: Path) -> None:
    lock = TicketLock(tmp_path, "org/repo", 7)
    lock.path.parent.mkdir(parents=True)
    dead = LockMetadata(2**22 + 12345, datetime.now(UTC) - timedelta(seconds=90))
    lock.path.write_text(json.dumps({"pid": dead.pid, "created_at": dead.created_at.isoformat()}))
    assert not pid_alive(dead.pid)
    with lock:
        assert lock.recovered is not None
        assert lock.recovered.pid == dead.pid
        assert lock.recovered.age_seconds >= 90
    assert lock.path.read_text() == ""
    assert read_metadata(lock.path) is None
    again = TicketLock(tmp_path, "org/repo", 7)
    with again:
        assert again.recovered is None


def test_read_metadata_tolerates_corrupt_content(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    assert read_metadata(path) is None
    for text in ["", "not json", "{}", "[]", '{"pid": "x", "created_at": 1}']:
        path.write_text(text)
        assert read_metadata(path) is None


def test_lock_conflict_message_names_pid_and_elapsed(tmp_path: Path) -> None:
    with TicketLock(tmp_path, "org/repo", 5) as held:
        with pytest.raises(TicketLocked) as conflict, TicketLock(tmp_path, "org/repo", 5):
            pass
        message = str(conflict.value)
        assert message.startswith(f"ticket #5 is already being run by process {os.getpid()} (")
        assert f"lock file {held.path}" in message
        assert conflict.value.pid == os.getpid()
        assert conflict.value.age_seconds is not None


def test_lock_conflict_with_dead_recorded_pid_says_not_running(tmp_path: Path) -> None:
    with TicketLock(tmp_path, "org/repo", 5) as held:
        held.path.write_text(
            json.dumps({"pid": 2**22 + 12345, "created_at": datetime.now(UTC).isoformat()})
        )
        with (
            pytest.raises(TicketLocked, match="not running but still holds the lock") as conflict,
            TicketLock(tmp_path, "org/repo", 5),
        ):
            pass
        assert "fuser" in str(conflict.value)
        assert "do not delete" in str(conflict.value)


def test_repo_lock_serialises_holders_without_process_local_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        first = RepoLock(tmp_path, tmp_path)
        second = RepoLock(tmp_path, tmp_path / "x" / "..")
        assert first.path == second.path
        order: list[str] = []
        async with first:
            with pytest.raises(RuntimeError, match="already acquired"):
                await first.__aenter__()

            async def waiter() -> None:
                async with second:
                    order.append("second")

            task = asyncio.create_task(waiter())
            await asyncio.sleep(0.2)
            assert order == []
            order.append("first")
        await asyncio.wait_for(task, 2)
        assert order == ["first", "second"]
        assert first.path.parent == tmp_path / "locks"
        assert first.path.name.startswith("repository-")

    asyncio.run(scenario())


def test_cancelled_repo_lock_wait_leaves_no_lock_held(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with RepoLock(tmp_path, tmp_path):
            waiter = asyncio.create_task(RepoLock(tmp_path, tmp_path).__aenter__())
            await asyncio.sleep(0.15)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        async with asyncio.timeout(2), RepoLock(tmp_path, tmp_path):
            pass

    asyncio.run(scenario())
