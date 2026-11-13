"""SQLite run store with durable operation intents and exact-head approvals."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from fugue.store.migrations import apply
from fugue.store.records import (
    AgentSession,
    Approval,
    Artifact,
    Operation,
    Run,
    StageAttempt,
    StageDuration,
    StoreError,
    UsageRecord,
    UsageTotals,
)


@dataclass(frozen=True)
class RepoUsageTotals(UsageTotals):
    """Usage totals that also name the repository when the group is tied to one."""

    repo: str | None


@dataclass(frozen=True)
class RepoStageDuration(StageDuration):
    """A stage attempt duration that names the repository of its run."""

    repo: str


_USAGE_GROUPS = {
    "role": "u.role",
    "provider": "u.provider",
    "run": "u.run_id",
    "ticket": "r.ticket_number",
}

_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cost_estimate_usd",
    "duration_ms",
)


def _record[T](record_type: type[T], row: sqlite3.Row, exclude: frozenset[str] = frozenset()) -> T:
    values = {key: value for key, value in dict(row).items() if key not in exclude}
    for key, value in values.items():
        if key.endswith("_at") and value is not None:
            values[key] = datetime.fromisoformat(value)
    return record_type(**values)


class Store:
    """Persist each mutation in a transaction before returning to the caller."""

    def __init__(
        self,
        path: Path | Literal[":memory:"],
        clock: Callable[[], datetime] | None = None,
        busy_timeout_seconds: float = 30.0,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._connection = sqlite3.connect(str(path), timeout=busy_timeout_seconds)
        self._connection.row_factory = sqlite3.Row
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            if path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
            apply(self._connection)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _now(self) -> str:
        return self._clock().isoformat()

    def _one[T](self, record_type: type[T], query: str, parameters: tuple[object, ...]) -> T:
        row = self._connection.execute(query, parameters).fetchone()
        if row is None:
            raise StoreError("Record does not exist")
        return _record(record_type, row)

    def _insert[T](self, record_type: type[T], table: str, values: Mapping[str, object]) -> T:
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        with self._connection:
            cursor = self._connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(values.values())
            )
            return self._one(
                record_type, f"SELECT * FROM {table} WHERE id = ?", (cursor.lastrowid,)
            )

    def _update(self, table: str, id: int, values: Mapping[str, object]) -> None:
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self._connection:
            cursor = self._connection.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?", (*values.values(), id)
            )
            if cursor.rowcount != 1:
                raise StoreError(f"No {table} record with id {id}")

    def create_run(self, ticket_number: int, repo: str) -> Run:
        now = self._now()
        return self._insert(
            Run,
            "runs",
            dict(
                ticket_number=ticket_number,
                repo=repo,
                state="created",
                created_at=now,
                updated_at=now,
            ),
        )

    def get_run(self, id: int) -> Run:
        return self._one(Run, "SELECT * FROM runs WHERE id = ?", (id,))

    def set_run_state(self, id: int, state: str) -> None:
        self._update("runs", id, dict(state=state, updated_at=self._now()))

    def list_runs(self, ticket_number: int | None = None, repo: str | None = None) -> list[Run]:
        query = "SELECT * FROM runs"
        clauses: list[str] = []
        parameters: list[object] = []
        if ticket_number is not None:
            clauses.append("ticket_number = ?")
            parameters.append(ticket_number)
        if repo is not None:
            clauses.append("repo = ?")
            parameters.append(repo)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        return [
            _record(Run, row)
            for row in self._connection.execute(query + " ORDER BY id", tuple(parameters))
        ]

    def begin_attempt(self, run_id: int, stage: str) -> StageAttempt:
        # Allocate and insert under one write lock, including across Store instances.
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT COALESCE(MAX(attempt), 0) + 1 FROM stage_attempts "
                "WHERE run_id = ? AND stage = ?",
                (run_id, stage),
            ).fetchone()
            return self._insert(
                StageAttempt,
                "stage_attempts",
                dict(
                    run_id=run_id,
                    stage=stage,
                    attempt=row[0],
                    status="running",
                    started_at=self._now(),
                ),
            )

    def finish_attempt(self, id: int, status: str, detail: str | None = None) -> None:
        self._update(
            "stage_attempts", id, dict(status=status, detail=detail, finished_at=self._now())
        )

    def stage_attempts(self, run_id: int, stage: str | None = None) -> list[StageAttempt]:
        query = "SELECT * FROM stage_attempts WHERE run_id = ?"
        parameters: list[object] = [run_id]
        if stage is not None:
            query += " AND stage = ?"
            parameters.append(stage)
        return [
            _record(StageAttempt, row)
            for row in self._connection.execute(query + " ORDER BY id", tuple(parameters))
        ]

    def agent_sessions(
        self,
        run_id: int,
        role: str | None = None,
        provider: str | None = None,
        authentication_mode: str | None = None,
    ) -> list[AgentSession]:
        query = "SELECT * FROM agent_sessions WHERE run_id = ?"
        parameters: list[object] = [run_id]
        for column, value in (
            ("role", role),
            ("provider", provider),
            ("authentication_mode", authentication_mode),
        ):
            if value is not None:
                query += f" AND {column} = ?"
                parameters.append(value)
        return [
            _record(AgentSession, row)
            for row in self._connection.execute(query + " ORDER BY id", tuple(parameters))
        ]

    def begin_operation(
        self, run_id: int, kind: str, key: str, intent: Mapping[str, Any]
    ) -> Operation:
        now = self._now()
        with self._connection:
            self._connection.execute(
                "INSERT INTO operations "
                "(run_id, kind, key, intent_json, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'intent', ?, ?) ON CONFLICT(run_id, kind, key) DO NOTHING",
                (run_id, kind, key, json.dumps(dict(intent)), now, now),
            )
            return self._one(
                Operation,
                "SELECT * FROM operations WHERE run_id = ? AND kind = ? AND key = ?",
                (run_id, kind, key),
            )

    def complete_operation(self, id: int, result: Mapping[str, Any]) -> None:
        self._update(
            "operations",
            id,
            dict(status="done", result_json=json.dumps(dict(result)), updated_at=self._now()),
        )

    def fail_operation(self, id: int, error: str) -> None:
        self._update(
            "operations",
            id,
            dict(status="failed", result_json=json.dumps({"error": error}), updated_at=self._now()),
        )

    def mark_reconciled(self, id: int, result: Mapping[str, Any]) -> None:
        self._update(
            "operations",
            id,
            dict(status="reconciled", result_json=json.dumps(dict(result)), updated_at=self._now()),
        )

    def pending_operations(self, run_id: int) -> list[Operation]:
        return [
            _record(Operation, row)
            for row in self._connection.execute(
                "SELECT * FROM operations WHERE run_id = ? AND status = 'intent' ORDER BY id",
                (run_id,),
            )
        ]

    def record_approval(
        self,
        run_id: int,
        kind: str,
        subject: str,
        head_sha: str,
        decision: str,
        decided_by: str,
        reason: str,
    ) -> Approval:
        return self._insert(
            Approval,
            "approvals",
            dict(
                run_id=run_id,
                kind=kind,
                subject=subject,
                head_sha=head_sha,
                decision=decision,
                decided_by=decided_by,
                reason=reason,
                created_at=self._now(),
            ),
        )

    def valid_approval(
        self, run_id: int, kind: str, subject: str, head_sha: str
    ) -> Approval | None:
        row = self._connection.execute(
            "SELECT * FROM approvals WHERE run_id = ? AND kind = ? AND subject = ? "
            "AND head_sha = ? AND decision = 'approved' AND invalidated_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (run_id, kind, subject, head_sha),
        ).fetchone()
        return _record(Approval, row) if row is not None else None

    def invalidate_approvals(self, run_id: int, subject: str, except_head_sha: str) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE approvals SET invalidated_at = ? WHERE run_id = ? AND subject = ? "
                "AND head_sha != ? AND invalidated_at IS NULL",
                (self._now(), run_id, subject, except_head_sha),
            )

    def record_agent_session(
        self, run_id: int, role: str, provider: str, authentication_mode: str, session_id: str
    ) -> AgentSession:
        return self._insert(
            AgentSession,
            "agent_sessions",
            dict(
                run_id=run_id,
                role=role,
                provider=provider,
                authentication_mode=authentication_mode,
                session_id=session_id,
                created_at=self._now(),
            ),
        )

    def record_usage(
        self,
        run_id: int,
        role: str,
        provider: str,
        authentication_mode: str,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        cost_estimate_usd: float | None = None,
        duration_ms: int | None = None,
    ) -> UsageRecord:
        return self._insert(
            UsageRecord,
            "usage_records",
            dict(
                run_id=run_id,
                role=role,
                provider=provider,
                authentication_mode=authentication_mode,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                cost_estimate_usd=cost_estimate_usd,
                duration_ms=duration_ms,
                created_at=self._now(),
            ),
        )

    def usage_summary(self, run_id: int) -> dict[str, int | float | None]:
        """Sum reported usage; retain NULL when no values were reported."""
        columns = ", ".join(f"SUM({field}) AS {field}" for field in _USAGE_FIELDS)
        row = self._connection.execute(
            f"SELECT {columns} FROM usage_records WHERE run_id = ?", (run_id,)
        ).fetchone()
        return {field: row[field] for field in _USAGE_FIELDS}

    def usage_by(
        self,
        group_by: Literal["role", "provider", "run", "ticket"],
        ticket_number: int | None = None,
        repo: str | None = None,
    ) -> list[RepoUsageTotals]:
        """Sum usage records per group; a sum stays NULL when nothing was reported."""
        expression = _USAGE_GROUPS.get(group_by)
        if expression is None:
            raise StoreError(f"Cannot group usage by {group_by!r}")
        sums = ", ".join(f"SUM(u.{field}) AS {field}" for field in _USAGE_FIELDS)
        # Ticket and run groups belong to one repository; ticket numbers repeat across them.
        with_repo = group_by in {"ticket", "run"}
        repo_select = "r.repo" if with_repo else "NULL"
        grouping = f"r.repo, {expression}" if with_repo else expression
        query = (
            f"SELECT {expression} AS group_key, {repo_select} AS group_repo, "
            f"COUNT(*) AS records, {sums} "
            "FROM usage_records u JOIN runs r ON r.id = u.run_id"
        )
        clauses: list[str] = []
        parameters: list[object] = []
        if ticket_number is not None:
            clauses.append("r.ticket_number = ?")
            parameters.append(ticket_number)
        if repo is not None:
            clauses.append("r.repo = ?")
            parameters.append(repo)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += f" GROUP BY {grouping} ORDER BY {grouping}"
        return [
            RepoUsageTotals(
                repo=row["group_repo"],
                key=str(row["group_key"]),
                records=row["records"],
                input_tokens=row["input_tokens"],
                cached_input_tokens=row["cached_input_tokens"],
                output_tokens=row["output_tokens"],
                reasoning_tokens=row["reasoning_tokens"],
                cost_estimate_usd=row["cost_estimate_usd"],
                duration_ms=row["duration_ms"],
            )
            for row in self._connection.execute(query, tuple(parameters))
        ]

    def stage_durations(
        self, run_id: int | None = None, repo: str | None = None
    ) -> list[RepoStageDuration]:
        """Elapsed time per stage attempt, optionally for one run or repository."""
        query = (
            "SELECT s.*, r.repo AS run_repo FROM stage_attempts s JOIN runs r ON r.id = s.run_id"
        )
        clauses: list[str] = []
        parameters: list[object] = []
        if run_id is not None:
            clauses.append("s.run_id = ?")
            parameters.append(run_id)
        if repo is not None:
            clauses.append("r.repo = ?")
            parameters.append(repo)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        durations: list[RepoStageDuration] = []
        for row in self._connection.execute(query + " ORDER BY s.run_id, s.id", tuple(parameters)):
            attempt = _record(StageAttempt, row, exclude=frozenset({"run_repo"}))
            seconds = (
                None
                if attempt.finished_at is None
                else (attempt.finished_at - attempt.started_at).total_seconds()
            )
            durations.append(
                RepoStageDuration(
                    run_id=attempt.run_id,
                    stage=attempt.stage,
                    attempt=attempt.attempt,
                    status=attempt.status,
                    duration_seconds=seconds,
                    repo=row["run_repo"],
                )
            )
        return durations

    def register_artifact(
        self, run_id: int, name: str, path: str, sha256: str, size_bytes: int
    ) -> Artifact:
        return self._insert(
            Artifact,
            "artifacts",
            dict(
                run_id=run_id,
                name=name,
                path=path,
                sha256=sha256,
                size_bytes=size_bytes,
                created_at=self._now(),
            ),
        )

    def list_artifacts(self, run_id: int) -> list[Artifact]:
        return [
            _record(Artifact, row)
            for row in self._connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? ORDER BY id", (run_id,)
            )
        ]
