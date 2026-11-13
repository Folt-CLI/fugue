"""Immutable records persisted by the run store."""

from dataclasses import dataclass
from datetime import datetime


class StoreError(Exception):
    """The run store cannot perform the requested action."""


@dataclass(frozen=True)
class Run:
    id: int
    ticket_number: int
    repo: str
    state: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class StageAttempt:
    id: int
    run_id: int
    stage: str
    attempt: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    detail: str | None


@dataclass(frozen=True)
class Operation:
    id: int
    run_id: int
    kind: str
    key: str
    intent_json: str
    result_json: str | None
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Approval:
    id: int
    run_id: int
    kind: str
    subject: str
    head_sha: str
    decision: str
    decided_by: str
    reason: str
    created_at: datetime
    invalidated_at: datetime | None


@dataclass(frozen=True)
class AgentSession:
    id: int
    run_id: int
    role: str
    provider: str
    authentication_mode: str
    session_id: str
    created_at: datetime


@dataclass(frozen=True)
class UsageRecord:
    id: int
    run_id: int
    role: str
    provider: str
    authentication_mode: str
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cost_estimate_usd: float | None
    duration_ms: int | None
    created_at: datetime


@dataclass(frozen=True)
class UsageTotals:
    """Summed usage records for one group; a field is None when no record reported it."""

    key: str
    records: int
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cost_estimate_usd: float | None
    duration_ms: int | None


@dataclass(frozen=True)
class StageDuration:
    """Elapsed time of one stage attempt; None while the attempt has not finished."""

    run_id: int
    stage: str
    attempt: int
    status: str
    duration_seconds: float | None


@dataclass(frozen=True)
class Artifact:
    id: int
    run_id: int
    name: str
    path: str
    sha256: str
    size_bytes: int
    created_at: datetime
