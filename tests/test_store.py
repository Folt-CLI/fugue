import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fugue.store import Store, StoreError
from fugue.store.migrations import MIGRATIONS, apply


@pytest.fixture
def store() -> Iterator[Store]:
    with Store(":memory:") as instance:
        yield instance


def test_migration_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    with Store(path) as store:
        run = store.create_run(12, "Folt-CLI/fugue")
        store.set_run_state(run.id, "implement")
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with Store(path) as reopened:
        assert reopened.get_run(run.id).state == "implement"
        assert reopened.list_runs() == [reopened.get_run(run.id)]


def test_parent_tables_exist_in_fresh_database(store: Store) -> None:
    tables = {
        row[0]
        for row in store._connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"parent_runs", "parent_run_work"} <= tables


def test_parent_migration_preserves_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        for migration in MIGRATIONS[:-1]:
            connection.executescript(migration)
        connection.execute(f"PRAGMA user_version = {len(MIGRATIONS) - 1}")
        connection.execute(
            "INSERT INTO runs VALUES (1, 12, 'o/r', 'implement', ?, ?)",
            ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO usage_records "
            "(run_id, role, provider, authentication_mode, input_tokens, created_at, model, tier) "
            "VALUES (1, 'implementer', 'provider', 'api_key', 42, ?, 'model', 'small')",
            ("2026-01-01T00:00:00+00:00",),
        )
        tables = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        before = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall() for table in tables
        }
    with Store(path) as migrated:
        remaining = {
            row[0]
            for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table, rows in before.items():
            if table.startswith("stack_approval"):
                # Parent runs no longer build stacks, so their approval tables are dropped.
                assert table not in remaining
                continue
            assert [
                tuple(row) for row in migrated._connection.execute(f"SELECT * FROM {table}")
            ] == rows
        assert migrated.get_run(1).state == "implement"
        parent = migrated.create_parent_run(170, "o/r")
        migrated.add_work(parent.id, 12, 0)
        migrated.set_work_run(parent.id, 12, 1)
        assert migrated.list_work(parent.id)[0].run_id == 1
        assert parent.feature_run_id is None
        migrated.set_feature_run(parent.id, 1)
        migrated.set_feature_run(parent.id, 1)
        assert migrated.get_parent_run(parent.id).feature_run_id == 1
        with pytest.raises(StoreError, match="another feature run"):
            migrated.set_feature_run(parent.id, 2)
        assert migrated._connection.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_parent_run_lookup_state_clock_and_frozen_records() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with Store(":memory:", clock=lambda: now) as store:
        assert store.find_open_parent_run("o/r", 170) is None
        parent = store.create_parent_run(170, "o/r")
        assert parent.repo == "o/r"
        assert parent.parent_number == 170
        assert parent.state == "created"
        assert parent.created_at == now
        assert store.find_open_parent_run("o/r", 170) == parent
        assert store.find_open_parent_run("x/y", 170) is None
        assert store.find_open_parent_run("o/r", 171) is None
        work = store.add_work(parent.id, 197, 0)
        with pytest.raises(FrozenInstanceError):
            parent.state = "completed"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            work.position = 1  # type: ignore[misc]
        store.set_parent_state(parent.id, "blocked")
        pending = store.find_open_parent_run("o/r", 170)
        assert pending is not None
        assert pending.state == "blocked"
        assert pending.created_at == now
        store.set_parent_state(parent.id, "completed")
        assert store.find_open_parent_run("o/r", 170) is None
        replacement = store.create_parent_run(170, "o/r")
        assert store.find_open_parent_run("o/r", 170) == replacement
        assert store.list_runs() == []


def test_parent_work_order_association_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "parents.db"
    with Store(path) as store:
        parent = store.create_parent_run(170, "o/r")
        other = store.create_parent_run(171, "o/r")
        last = store.add_work(parent.id, 199, 2)
        first = store.add_work(parent.id, 197, 0)
        middle = store.add_work(parent.id, 198, 1)
        separate = store.add_work(other.id, 197, 0)
        assert last.run_id is None
        assert store.list_work(parent.id) == [first, middle, last]
        assert store.list_work(other.id) == [separate]
        assert store.list_work(999) == []
        run = store.create_run(197, "o/r")
        store.set_work_run(parent.id, 197, run.id)
        assert store.list_work(other.id) == [separate]
        store.set_parent_state(parent.id, "implement")
    with Store(path) as reopened:
        pending = reopened.find_open_parent_run("o/r", 170)
        assert pending is not None
        assert pending.id == parent.id
        assert pending.state == "implement"
        work = reopened.list_work(parent.id)
        assert [item.ticket_number for item in work] == [197, 198, 199]
        assert work[0].run_id == run.id
        assert work[1:] == [middle, last]
        reopened.set_work_run(parent.id, 197, None)
        assert reopened.list_work(parent.id) == [first, middle, last]


def test_parent_work_constraints_and_failed_write_recovery(store: Store) -> None:
    parent = store.create_parent_run(170, "o/r")
    work = store.add_work(parent.id, 197, 0)
    with pytest.raises(sqlite3.IntegrityError):
        store.add_work(parent.id, 197, 1)
    with pytest.raises(sqlite3.IntegrityError):
        store.add_work(999, 197, 0)
    with pytest.raises(sqlite3.IntegrityError):
        store.set_work_run(parent.id, 197, 999)
    assert store.list_work(parent.id) == [work]
    run = store.create_run(197, "o/r")
    store.set_work_run(parent.id, 197, run.id)
    assert store.list_work(parent.id)[0].run_id == run.id


def test_missing_parent_records(store: Store) -> None:
    parent = store.create_parent_run(170, "o/r")
    store.add_work(parent.id, 197, 0)
    with pytest.raises(StoreError):
        store.set_parent_state(999, "completed")
    with pytest.raises(StoreError):
        store.set_work_run(999, 197, None)
    with pytest.raises(StoreError):
        store.set_work_run(parent.id, 198, None)
    store.set_parent_state(parent.id, "completed")
    assert store.find_open_parent_run("o/r", 170) is None


def test_merge_approvals_lists_valid_approvals_of_one_repository(store: Store) -> None:
    run = store.create_run(1, "o/r")
    other = store.create_run(2, "x/y")
    valid = store.record_approval(run.id, "merge", "7", "a" * 40, "approved", "dev", "ok")
    store.record_approval(run.id, "merge", "8", "b" * 40, "rejected", "dev", "no")
    store.record_approval(run.id, "other", "9", "c" * 40, "approved", "dev", "ok")
    store.record_approval(other.id, "merge", "7", "d" * 40, "approved", "dev", "ok")
    stale = store.record_approval(run.id, "merge", "10", "e" * 40, "approved", "dev", "ok")
    store.invalidate_approvals(run.id, "10", "f" * 40)
    assert [a.id for a in store.merge_approvals("o/r")] == [valid.id]
    assert stale.id not in [a.id for a in store.merge_approvals("o/r")]
    assert [a.subject for a in store.merge_approvals("x/y")] == ["7"]
    assert store.merge_approvals("none/none") == []


def _hold_write_lock(path: Path) -> sqlite3.Connection:
    holder = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    return holder


def test_write_waits_for_busy_timeout_and_succeeds_when_released(tmp_path: Path) -> None:
    path = tmp_path / "busy.db"
    with Store(path, busy_timeout_seconds=10) as store:
        holder = _hold_write_lock(path)
        timer = threading.Timer(0.4, holder.rollback)
        timer.start()
        try:
            started = time.monotonic()
            run = store.create_run(1, "o/r")
            assert time.monotonic() - started >= 0.3
        finally:
            timer.join()
            holder.close()
        assert store.get_run(run.id).ticket_number == 1


def test_write_fails_with_locked_error_after_busy_timeout(tmp_path: Path) -> None:
    path = tmp_path / "busy.db"
    with Store(path, busy_timeout_seconds=0.2) as store:
        holder = _hold_write_lock(path)
        try:
            started = time.monotonic()
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                store.create_run(1, "o/r")
            assert 0.15 <= time.monotonic() - started < 5
        finally:
            holder.rollback()
            holder.close()


def test_busy_timeout_defaults_to_thirty_seconds(tmp_path: Path) -> None:
    with Store(tmp_path / "default.db") as store:
        assert store._connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_newer_version_errors(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    with pytest.raises(StoreError, match="newer"):
        Store(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS) + 1


def test_failed_migration_rolls_back() -> None:
    connection = sqlite3.connect(":memory:")
    MIGRATIONS.append("CREATE TABLE temporary_table (id INTEGER); INVALID SQL;")
    try:
        with pytest.raises(sqlite3.OperationalError):
            apply(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        )
    finally:
        MIGRATIONS.pop()
        connection.close()


def test_run_clock_filter_and_frozen_record() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with Store(":memory:", clock=lambda: now) as store:
        run = store.create_run(12, "Folt-CLI/fugue")
        other = store.create_run(13, "Folt-CLI/fugue")
        assert run.created_at == run.updated_at == now
        with pytest.raises(FrozenInstanceError):
            run.state = "repair"  # type: ignore[misc]
        now += timedelta(seconds=1)
        store.set_run_state(run.id, "verify")
        assert store.get_run(run.id).updated_at == now
        assert store.get_run(run.id).created_at == run.created_at
        assert store.list_runs(12) == [store.get_run(run.id)]
        assert store.list_runs(13) == [other]
        assert store.list_runs(99) == []


def test_attempt_numbers_and_finish(store: Store) -> None:
    run = store.create_run(12, "Folt-CLI/fugue")
    other = store.create_run(13, "Folt-CLI/fugue")
    first = store.begin_attempt(run.id, "implement")
    assert first.attempt == 1
    assert first.finished_at is None
    store.finish_attempt(first.id, "failed", "repair required")
    assert store.begin_attempt(run.id, "implement").attempt == 2
    assert store.begin_attempt(run.id, "verify").attempt == 1
    assert store.begin_attempt(other.id, "implement").attempt == 1


def test_attempt_finish_persists(tmp_path: Path) -> None:
    path = tmp_path / "attempts.db"
    with Store(path) as store:
        run = store.create_run(12, "Folt-CLI/fugue")
        attempt = store.begin_attempt(run.id, "verify")
        store.finish_attempt(attempt.id, "done", "passed")
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT status, detail, finished_at FROM stage_attempts WHERE id = ?", (attempt.id,)
        ).fetchone()
        assert row[:2] == ("done", "passed")
        assert datetime.fromisoformat(row[2]) >= attempt.started_at


@pytest.mark.parametrize("outcome", ["done", "failed", "reconciled"])
def test_operation_retry_and_transitions(store: Store, outcome: str) -> None:
    run = store.create_run(12, "Folt-CLI/fugue")
    operation = store.begin_operation(run.id, "github", "create-pr", {"title": "Feature"})
    assert operation.status == "intent"
    assert operation.result_json is None
    assert store.begin_operation(run.id, "github", "create-pr", {"title": "changed"}) == operation
    if outcome == "done":
        store.complete_operation(operation.id, {"number": 42})
    elif outcome == "failed":
        store.fail_operation(operation.id, "unavailable")
    else:
        store.mark_reconciled(operation.id, {"number": 42})
    result = store.begin_operation(run.id, "github", "create-pr", {})
    assert result.id == operation.id
    assert result.status == outcome
    assert json.loads(result.intent_json) == {"title": "Feature"}
    assert result.result_json is not None
    assert json.loads(result.result_json) == (
        {"error": "unavailable"} if outcome == "failed" else {"number": 42}
    )
    assert store.pending_operations(run.id) == []


def test_pending_operations_are_scoped(store: Store) -> None:
    run = store.create_run(12, "Folt-CLI/fugue")
    other = store.create_run(13, "Folt-CLI/fugue")
    pending = store.begin_operation(run.id, "git", "branch", {})
    done = store.begin_operation(run.id, "github", "branch", {})
    store.complete_operation(done.id, {})
    store.begin_operation(other.id, "git", "branch", {})
    assert store.pending_operations(run.id) == [pending]


def test_approval_exact_head_latest_and_invalidation(store: Store) -> None:
    run = store.create_run(12, "Folt-CLI/fugue")
    old = store.record_approval(run.id, "merge", "PR42", "abc", "approved", "developer", "ready")
    assert store.valid_approval(run.id, "merge", "PR42", "abc") == old
    assert store.valid_approval(run.id, "merge", "PR42", "def") is None
    assert store.valid_approval(run.id, "select", "PR42", "abc") is None
    assert store.valid_approval(run.id, "merge", "PR43", "abc") is None
    new = store.record_approval(run.id, "merge", "PR42", "def", "approved", "developer", "ready")
    latest = store.record_approval(
        run.id, "merge", "PR42", "def", "approved", "developer", "confirmed"
    )
    store.invalidate_approvals(run.id, "PR42", except_head_sha="def")
    assert store.valid_approval(run.id, "merge", "PR42", "abc") is None
    assert store.valid_approval(run.id, "merge", "PR42", "def") == latest
    assert latest.id > new.id
    store.record_approval(run.id, "merge", "PR43", "abc", "rejected", "developer", "fix required")
    assert store.valid_approval(run.id, "merge", "PR43", "abc") is None
    with pytest.raises(sqlite3.IntegrityError):
        store.record_approval(run.id, "merge", "PR43", "abc", "unknown", "developer", "")


def test_usage_summary_and_session(store: Store) -> None:
    run = store.create_run(12, "Folt-CLI/fugue")
    other = store.create_run(13, "Folt-CLI/fugue")
    session = store.record_agent_session(run.id, "implementer", "codex", "api_key", "session-1")
    assert session.session_id == "session-1"
    assert session.run_id == run.id
    first = store.record_usage(
        run.id,
        "implementer",
        "codex",
        "api_key",
        input_tokens=10,
        cached_input_tokens=4,
        output_tokens=5,
        reasoning_tokens=2,
        cost_estimate_usd=0.1,
        duration_ms=100,
    )
    assert first.input_tokens == 10
    store.record_usage(
        run.id,
        "reviewer",
        "codex",
        "api_key",
        input_tokens=3,
        output_tokens=2,
        cost_estimate_usd=0.2,
        duration_ms=50,
    )
    store.record_usage(run.id, "reviewer", "codex", "subscription_login")
    store.record_usage(other.id, "reviewer", "codex", "api_key", input_tokens=1000)
    summary = store.usage_summary(run.id)
    assert summary == dict(
        input_tokens=13,
        cached_input_tokens=4,
        output_tokens=7,
        reasoning_tokens=2,
        cost_estimate_usd=pytest.approx(0.3),
        duration_ms=150,
    )
    assert all(value is None for value in store.usage_summary(999).values())


def test_artifacts(store: Store) -> None:
    run = store.create_run(12, "Folt-CLI/fugue")
    artifact = store.register_artifact(run.id, "findings", "findings.md", "a" * 64, 10)
    assert store.list_artifacts(run.id) == [artifact]
    assert store.list_artifacts(999) == []
    assert artifact.sha256 == "a" * 64


@pytest.mark.parametrize(
    "record_kind", ["attempt", "operation", "approval", "session", "usage", "artifact"]
)
def test_foreign_keys(store: Store, record_kind: str) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        if record_kind == "attempt":
            store.begin_attempt(999, "verify")
        elif record_kind == "operation":
            store.begin_operation(999, "git", "branch", {})
        elif record_kind == "approval":
            store.record_approval(999, "merge", "PR42", "abc", "approved", "developer", "ready")
        elif record_kind == "session":
            store.record_agent_session(999, "reviewer", "codex", "api_key", "session")
        elif record_kind == "usage":
            store.record_usage(999, "reviewer", "codex", "api_key")
        else:
            store.register_artifact(999, "log", "log.txt", "a" * 64, 0)
    # A failed mutation must not leave a transaction that prevents the next write.
    assert store.begin_attempt(store.create_run(12, "Folt-CLI/fugue").id, "verify").attempt == 1


def test_missing_records(store: Store) -> None:
    with pytest.raises(StoreError):
        store.get_run(999)
    with pytest.raises(StoreError):
        store.set_run_state(999, "verify")
    with pytest.raises(StoreError):
        store.finish_attempt(999, "done")
    with pytest.raises(StoreError):
        store.complete_operation(999, {})


def test_context_manager_closes_store() -> None:
    with Store(":memory:") as store:
        store.create_run(12, "Folt-CLI/fugue")
    store.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.list_runs()


# Scenario 1
def test_stage_attempts_are_ordered_and_filtered(store: Store) -> None:
    run = store.create_run(12, "Folt-CLI/fugue")
    other = store.create_run(13, "Folt-CLI/fugue")
    first = store.begin_attempt(run.id, "implement")
    verify = store.begin_attempt(run.id, "verify")
    second = store.begin_attempt(run.id, "implement")
    store.begin_attempt(other.id, "implement")
    assert [a.id for a in store.stage_attempts(run.id)] == [first.id, verify.id, second.id]
    assert [a.id for a in store.stage_attempts(run.id, "implement")] == [first.id, second.id]
    assert store.stage_attempts(run.id, "repair") == []
    assert store.stage_attempts(999) == []


# Scenario 1
def test_agent_sessions_are_ordered_and_filtered(store: Store) -> None:
    run = store.create_run(12, "Folt-CLI/fugue")
    other = store.create_run(13, "Folt-CLI/fugue")
    first = store.record_agent_session(run.id, "implementer", "codex", "api_key", "s1")
    reviewer = store.record_agent_session(run.id, "reviewer", "codex", "api_key", "s2")
    third = store.record_agent_session(run.id, "implementer", "claude", "subscription_login", "s3")
    store.record_agent_session(other.id, "implementer", "codex", "api_key", "s4")
    assert [s.id for s in store.agent_sessions(run.id)] == [first.id, reviewer.id, third.id]
    assert [s.id for s in store.agent_sessions(run.id, role="implementer")] == [first.id, third.id]
    assert [s.id for s in store.agent_sessions(run.id, provider="claude")] == [third.id]
    assert [s.id for s in store.agent_sessions(run.id, authentication_mode="api_key")] == [
        first.id,
        reviewer.id,
    ]
    assert [
        s.id
        for s in store.agent_sessions(
            run.id, role="implementer", provider="codex", authentication_mode="api_key"
        )
    ] == [first.id]
    assert store.agent_sessions(run.id, role="analyst") == []
    assert store.agent_sessions(999) == []
