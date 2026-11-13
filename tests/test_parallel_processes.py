"""Parallel runs across real operating-system processes with a fake engine."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fugue.engine.locks import TicketLock
from fugue.store import Store

HELPER = """
import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from fugue.cli import app as cli
from fugue.cli import wiring
from fugue.engine import RunOutcome
from fugue.engine.locks import RepoLock
from fugue.store import Store
from fugue.workflow import RunState

MODE = os.environ["FAKE_MODE"]
CONTROL = Path(os.environ["FAKE_CONTROL"])
TAG = os.environ.get("FAKE_TAG", "p")
OTHER = os.environ.get("FAKE_OTHER", "q")
REPO = os.environ.get("FAKE_REPO", "o/r")
HOLD = os.environ.get("FAKE_HOLD") == "1"


def touch(name):
    (CONTROL / name).write_text(str(os.getpid()))


def log(line):
    with (CONTROL / "log").open("a") as handle:
        handle.write(line + "\\n")


async def wait_for(name, timeout=60.0):
    deadline = time.monotonic() + timeout
    while not (CONTROL / name).exists():
        if time.monotonic() > deadline:
            raise TimeoutError(name)
        await asyncio.sleep(0.02)


class Engine:
    def __init__(self, config, repo_root):
        self.wait = config.limits.database_wait_seconds
        self.repo_root = repo_root
        state_dir = wiring.resolve_state_dir(config, os.environ, Path.home())
        self.deps = SimpleNamespace(
            state_dir=state_dir, repo_root=repo_root, github=SimpleNamespace(repo=REPO)
        )

    def close(self):
        pass

    async def run_to_green(self, number):
        return await self.run_to_pr(number)

    async def run_to_pr(self, number):
        state_dir = self.deps.state_dir
        if MODE == "hold":
            store = Store(state_dir / "fugue.db", busy_timeout_seconds=self.wait)
            unfinished = [
                run
                for run in store.list_runs(number, REPO)
                if run.state not in ("completed", "failed")
            ]
            run = unfinished[-1] if unfinished else store.create_run(number, REPO)
            store.set_run_state(run.id, "running")
            touch(TAG + "-ready")
            if HOLD:
                await wait_for("release")
            store.set_run_state(run.id, RunState.WAITING_FOR_CI)
            store.close()
            return RunOutcome(run.id, RunState.WAITING_FOR_CI)
        if MODE == "prepare":
            async with RepoLock(state_dir, self.repo_root):
                log(TAG + "-enter")
                await asyncio.sleep(0.6)
                log(TAG + "-exit")
            touch(TAG + "-agent")
            await wait_for(OTHER + "-agent")
        elif MODE == "repo_hold":
            async with RepoLock(state_dir, self.repo_root):
                touch(TAG + "-ready")
                await wait_for("release")
        elif MODE == "repo_wait":
            touch(TAG + "-waiting")
            async with RepoLock(state_dir, self.repo_root):
                touch(TAG + "-got")
        elif MODE == "db":
            touch("db-attempt")
            store = Store(state_dir / "fugue.db", busy_timeout_seconds=self.wait)
            store.create_run(number, REPO)
            store.close()
        return RunOutcome(number, RunState.WAITING_FOR_CI)


cli.engine_factory = lambda config, root, progress: Engine(config, root)
cli.app(args=sys.argv[1:], prog_name="fugue")
"""

TIMEOUT = 60.0


class Harness:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo = root / "repo"
        self.repo.mkdir()
        self.control = root / "control"
        self.control.mkdir()
        self.state_dir = root / "state" / "fugue"
        self.script = root / "scripts" / "helper.py"
        self.script.parent.mkdir()
        self.script.write_text(HELPER)
        self.processes: list[subprocess.Popen[str]] = []

    def spawn(
        self, mode: str, *tickets: int, tag: str = "p", **extra: str
    ) -> subprocess.Popen[str]:
        env = {
            **os.environ,
            "XDG_STATE_HOME": str(self.root / "state"),
            "FAKE_MODE": mode,
            "FAKE_CONTROL": str(self.control),
            "FAKE_TAG": tag,
            "NO_COLOR": "1",
            "COLUMNS": "200",
            **extra,
        }
        process = subprocess.Popen(
            [
                sys.executable,
                str(self.script),
                "run",
                *(str(ticket) for ticket in tickets),
                "--repo-root",
                str(self.repo),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=self.root,
        )
        self.processes.append(process)
        return process

    def wait_for(self, name: str) -> None:
        deadline = time.monotonic() + TIMEOUT
        while not (self.control / name).exists():
            assert time.monotonic() < deadline, f"timed out waiting for {name}"
            time.sleep(0.02)

    def release(self) -> None:
        (self.control / "release").write_text("go")

    def lock_path(self, ticket: int, repo: str = "o/r") -> Path:
        return TicketLock(self.state_dir, repo, ticket).path


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    instance = Harness(tmp_path)
    yield instance
    for process in instance.processes:
        if process.poll() is None:
            process.kill()
        if process.stdout is not None and not process.stdout.closed:
            process.communicate()


def finish(process: subprocess.Popen[str]) -> tuple[int, str]:
    output, _ = process.communicate(timeout=TIMEOUT)
    return process.returncode, output


def test_ticket_lock_conflict_exits_three_and_holder_is_unaffected(harness: Harness) -> None:
    holder = harness.spawn("hold", 5, tag="a", FAKE_HOLD="1")
    harness.wait_for("a-ready")
    started = time.monotonic()
    code, output = finish(harness.spawn("hold", 5, tag="b"))
    assert time.monotonic() - started < 30
    assert holder.poll() is None
    assert code == 3
    assert f"error: ticket #5 is already being run by process {holder.pid} (running " in output
    assert f"lock file {harness.lock_path(5)}" in output
    harness.release()
    holder_code, holder_output = finish(holder)
    assert holder_code == 0
    assert "waiting_for_ci" in holder_output


def test_different_tickets_and_repositories_never_conflict(harness: Harness) -> None:
    holder = harness.spawn("hold", 5, tag="a", FAKE_HOLD="1")
    harness.wait_for("a-ready")
    other_ticket, _ = finish(harness.spawn("hold", 6, tag="b"))
    other_repository, _ = finish(harness.spawn("hold", 5, tag="c", FAKE_REPO="o/other"))
    assert (other_ticket, other_repository) == (0, 0)
    harness.release()
    assert finish(holder)[0] == 0


def test_stale_lock_after_kill_is_recovered_and_run_resumes(harness: Harness) -> None:
    victim = harness.spawn("hold", 5, tag="a", FAKE_HOLD="1")
    harness.wait_for("a-ready")
    victim.send_signal(signal.SIGKILL)
    victim.communicate()
    code, output = finish(harness.spawn("hold", 5, tag="b"))
    assert code == 0
    assert f"note: recovered stale lock left by process {victim.pid} (" in output
    assert " ago); resuming" in output
    with Store(harness.state_dir / "fugue.db") as store:
        runs = store.list_runs(5)
    assert len(runs) == 1
    assert f"run {runs[0].id}: waiting_for_ci" in output
    # A clean release leaves the file empty and the next run notes no recovery.
    assert harness.lock_path(5).read_text() == ""
    code, output = finish(harness.spawn("hold", 5, tag="c"))
    assert code == 0
    assert "recovered" not in output
    assert harness.lock_path(5).read_text() == ""


def test_conflict_with_dead_recorded_process_says_not_running(harness: Harness) -> None:
    holder = harness.spawn("hold", 5, tag="a", FAKE_HOLD="1")
    harness.wait_for("a-ready")
    dead = {"pid": 2**22 + 77, "created_at": datetime.now(UTC).isoformat()}
    harness.lock_path(5).write_text(json.dumps(dead))
    code, output = finish(harness.spawn("hold", 5, tag="b"))
    assert code == 3
    assert "not running but still holds the lock" in output
    assert "fuser" in output
    assert "do not delete" in output
    harness.release()
    assert finish(holder)[0] == 0


def test_repository_lock_serialises_prepare_while_agents_overlap(harness: Harness) -> None:
    first = harness.spawn("prepare", 5, tag="a", FAKE_OTHER="b")
    second = harness.spawn("prepare", 6, tag="b", FAKE_OTHER="a")
    assert finish(first)[0] == 0
    assert finish(second)[0] == 0
    lines = (harness.control / "log").read_text().split()
    assert len(lines) == 4
    for index in (0, 2):
        tag = lines[index].removesuffix("-enter")
        assert lines[index] == f"{tag}-enter"
        assert lines[index + 1] == f"{tag}-exit"
    assert {lines[0][0], lines[2][0]} == {"a", "b"}
    # Each waited for the other's agent marker, so the agent phases overlapped.
    assert (harness.control / "a-agent").exists()
    assert (harness.control / "b-agent").exists()


def test_repository_lock_is_released_when_holder_is_killed(harness: Harness) -> None:
    holder = harness.spawn("repo_hold", 5, tag="a")
    harness.wait_for("a-ready")
    waiter = harness.spawn("repo_wait", 6, tag="b")
    harness.wait_for("b-waiting")
    time.sleep(0.5)
    assert not (harness.control / "b-got").exists()
    holder.send_signal(signal.SIGKILL)
    holder.communicate()
    assert finish(waiter)[0] == 0
    assert (harness.control / "b-got").exists()


def test_repository_lock_wait_cancelled_leaves_no_lock_held(harness: Harness) -> None:
    holder = harness.spawn("repo_hold", 5, tag="a")
    harness.wait_for("a-ready")
    waiter = harness.spawn("repo_wait", 6, tag="b")
    harness.wait_for("b-waiting")
    time.sleep(0.5)
    waiter.send_signal(signal.SIGINT)
    code, output = finish(waiter)
    assert code == 6
    assert "interrupted" in output
    harness.release()
    assert finish(holder)[0] == 0
    code, _ = finish(harness.spawn("repo_wait", 7, tag="c"))
    assert code == 0
    assert (harness.control / "c-got").exists()
    assert not (harness.control / "b-got").exists()


def hold_database_write_lock(harness: Harness) -> sqlite3.Connection:
    harness.state_dir.mkdir(parents=True)
    Store(harness.state_dir / "fugue.db").close()
    holder = sqlite3.connect(harness.state_dir / "fugue.db", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    return holder


def test_database_wait_succeeds_when_released_inside_window(harness: Harness) -> None:
    (harness.repo / "fugue.toml").write_text("[limits]\ndatabase_wait_seconds = 40\n")
    holder = hold_database_write_lock(harness)
    process = harness.spawn("db", 5)
    harness.wait_for("db-attempt")
    time.sleep(1.0)
    assert process.poll() is None
    holder.rollback()
    holder.close()
    code, output = finish(process)
    assert code == 0, output
    with Store(harness.state_dir / "fugue.db") as store:
        assert [run.ticket_number for run in store.list_runs()] == [5]


def test_database_wait_timeout_reports_failure_without_traceback(harness: Harness) -> None:
    (harness.repo / "fugue.toml").write_text("[limits]\ndatabase_wait_seconds = 1\n")
    holder = hold_database_write_lock(harness)
    try:
        code, output = finish(harness.spawn("db", 5))
    finally:
        holder.rollback()
        holder.close()
    assert code == 4
    assert "run database is locked by another fugue process; waited 1 s" in output
    assert "limits.database_wait_seconds" in output
    assert "Traceback" not in output
