"""Pure string renderers for command output."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fugue.config import FugueConfig
from fugue.engine import RunOutcome
from fugue.engine.review import Finding
from fugue.models import AuthenticationMode
from fugue.store import Run
from fugue.store.db import RepoStageDuration, RepoUsageTotals
from fugue.store.records import Approval
from fugue.workflow import RunState


@dataclass(frozen=True)
class AttemptLine:
    stage: str
    attempt: int
    status: str


@dataclass(frozen=True)
class OperationLine:
    kind: str
    key: str
    status: str


@dataclass(frozen=True)
class ApprovalLine:
    kind: str
    subject: str
    head_sha: str
    decision: str
    valid: bool


@dataclass(frozen=True)
class RunDetail:
    run: Run
    attempts: tuple[AttemptLine, ...]
    operations: tuple[OperationLine, ...]
    approvals: tuple[ApprovalLine, ...]
    usage: Mapping[str, int | float | None]


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    grid = [list(header), *(list(row) for row in rows)]
    widths = [max(len(row[column]) for row in grid) for column in range(len(header))]
    return [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in grid
    ]


def render_roles(config: FugueConfig) -> str:
    rows = [
        [str(role), role_config.provider, str(role_config.authentication_mode)]
        for role, role_config in config.roles.items()
    ]
    lines = _table(["role", "provider", "authentication mode"], rows)
    lines.extend(
        f"warning: role {role} uses api_key authentication (metered)"
        for role, role_config in config.roles.items()
        if role_config.authentication_mode == AuthenticationMode.API_KEY
    )
    return "\n".join(lines)


def render_outcome(outcome: RunOutcome) -> str:
    lines = [f"run {outcome.run_id}: {outcome.state}"]
    if outcome.pr_number is not None:
        lines.append(f"pull request: #{outcome.pr_number}")
    if outcome.head_sha is not None:
        lines.append(f"head SHA: {outcome.head_sha}")
    lines.extend(f"problem: {problem}" for problem in outcome.problems)
    lines.extend(f"note: {note}" for note in outcome.notes)
    if outcome.findings:
        lines.append(render_findings(outcome.findings))
    hints = {
        RunState.WAITING_FOR_CI: f"fugue review {outcome.run_id}",
        RunState.WAITING_FOR_DECISION: (
            f"fugue decide {outcome.run_id} --accept INDEX --reject 'INDEX:reason text'"
        ),
        RunState.WAITING_FOR_MERGE_APPROVAL: (
            f"fugue request-approval {outcome.run_id}; then "
            f"fugue approve {outcome.run_id} --reason 'reason text'"
        ),
        RunState.INTERRUPTED: f"fugue resume {outcome.run_id}",
        RunState.PROVIDER_LIMITED: f"fugue resume {outcome.run_id}",
    }
    hint = hints.get(outcome.state)
    if hint is not None:
        lines.append(f"next: {hint}")
    return "\n".join(lines)


def render_findings(findings: Sequence[Finding]) -> str:
    rows = [
        [
            str(f.index),
            "Blocking" if f.blocking else "Non-blocking",
            f.severity,
            f.location or "n/a",
            f.summary,
        ]
        for f in findings
    ]
    return "\n".join(_table(["index", "kind", "severity", "location", "summary"], rows))


def render_approval(approval: Approval) -> str:
    return "\n".join(
        [
            f"run {approval.run_id}: approval {approval.decision}",
            f"pull request: #{approval.subject}",
            f"head SHA: {approval.head_sha}",
            f"by: {approval.decided_by}",
            f"reason: {approval.reason}",
            "Approval is valid only for this exact head SHA.",
            f"next: fugue merge {approval.run_id}",
        ]
    )


def render_runs(runs: Sequence[Run]) -> str:
    if not runs:
        return "No runs."
    rows = [
        [
            str(run.id),
            run.repo,
            f"#{run.ticket_number}",
            run.state,
            run.updated_at.isoformat(timespec="seconds"),
        ]
        for run in runs
    ]
    return "\n".join(_table(["id", "repo", "ticket", "state", "updated"], rows))


def _section(title: str, header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    if not rows:
        return ["", f"{title}: none"]
    return ["", f"{title}:", *_table(header, rows)]


def _usage_value(value: int | float | None) -> str:
    return "n/a" if value is None else str(value)


def render_run_detail(detail: RunDetail) -> str:
    run = detail.run
    lines = [
        f"run {run.id}: ticket #{run.ticket_number} in {run.repo}",
        f"state: {run.state}",
        f"updated: {run.updated_at.isoformat(timespec='seconds')}",
    ]
    lines += _section(
        "stage attempts",
        ["stage", "attempt", "status"],
        [[a.stage, str(a.attempt), a.status] for a in detail.attempts],
    )
    lines += _section(
        "operations",
        ["kind", "key", "status"],
        [[o.kind, o.key, o.status] for o in detail.operations],
    )
    lines += _section(
        "approvals",
        ["kind", "subject", "head SHA", "decision", "valid"],
        [
            [a.kind, a.subject, a.head_sha, a.decision, "yes" if a.valid else "no"]
            for a in detail.approvals
        ],
    )
    lines += ["", "usage (reported values, not a bill):"]
    lines += [f"  {name}: {_usage_value(value)}" for name, value in detail.usage.items()]
    return "\n".join(lines)


def render_usage(totals: Sequence[RepoUsageTotals], group_by: str) -> str:
    """Render usage records per group; n/a marks values no provider reported."""
    if not totals:
        return "No usage records."
    with_repo = any(total.repo is not None for total in totals)
    rows = [
        [
            *([total.repo or ""] if with_repo else []),
            total.key,
            str(total.records),
            _usage_value(total.input_tokens),
            _usage_value(total.cached_input_tokens),
            _usage_value(total.output_tokens),
            _usage_value(total.reasoning_tokens),
            "n/a" if total.cost_estimate_usd is None else f"{total.cost_estimate_usd:.4f}",
            _usage_value(total.duration_ms),
        ]
        for total in totals
    ]
    header = [
        *(["repo"] if with_repo else []),
        group_by,
        "records",
        "input",
        "cached",
        "output",
        "reasoning",
        "cost estimate (USD)",
        "duration (ms)",
    ]
    note = "Cost is an estimate from reported usage, not a bill; n/a means not reported."
    return "\n".join([*_table(header, rows), "", note])


def render_timing(durations: Sequence[RepoStageDuration]) -> str:
    """Render stage attempt durations; unfinished attempts show n/a."""
    if not durations:
        return "No stage attempts."
    rows = [
        [
            str(d.run_id),
            d.repo,
            d.stage,
            str(d.attempt),
            d.status,
            "n/a" if d.duration_seconds is None else f"{d.duration_seconds:.1f}",
        ]
        for d in durations
    ]
    return "\n".join(_table(["run", "repo", "stage", "attempt", "status", "seconds"], rows))


def _toml_scalar(value: object) -> str:
    return json.dumps(value) if not isinstance(value, bool) else str(value).lower()


def _toml_lines(path: Sequence[str], table: Mapping[str, Any]) -> list[str]:
    scalars = {key: value for key, value in table.items() if not isinstance(value, dict)}
    tables = {key: value for key, value in table.items() if isinstance(value, dict)}
    lines = [f"{key} = {_toml_scalar(value)}" for key, value in scalars.items()]
    for key, value in tables.items():
        child = [*path, key]
        lines.extend(["", f"[{'.'.join(child)}]", *_toml_lines(child, value)])
    return lines


def render_config(config: FugueConfig) -> str:
    """Render the effective configuration; it holds no secrets."""
    data = config.model_dump(mode="json", exclude_none=True)
    return "\n".join(_toml_lines([], data)).strip()
