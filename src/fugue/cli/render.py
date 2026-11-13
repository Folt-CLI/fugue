"""Pure string renderers for command output."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from fugue.agents import UsageRecord, resolve_models
from fugue.cli import terminal
from fugue.config import FugueConfig
from fugue.engine import RunOutcome
from fugue.engine.locks import format_elapsed
from fugue.engine.review import Finding
from fugue.models import AuthenticationMode, Role, Size, Status
from fugue.overview import Overview, SummaryResult
from fugue.overview.collect import WAITING_RUN_STATES
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
class ModelLine:
    role: str
    size: str
    tier: str
    source: str
    models: str


@dataclass(frozen=True)
class RunDetail:
    run: Run
    attempts: tuple[AttemptLine, ...]
    operations: tuple[OperationLine, ...]
    approvals: tuple[ApprovalLine, ...]
    usage: Mapping[str, int | float | None]
    models: tuple[ModelLine, ...] = ()


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    return terminal.table_text(header, rows)


def describe_models(models: Mapping[str, str | None]) -> str:
    """Chain members in order, each with its model or `default` when it has none."""
    return " > ".join(f"{provider}:{model or 'default'}" for provider, model in models.items())


def _policy_text(config: FugueConfig, role: Role) -> str:
    rules = config.policy.tier_rules.get(role, {})
    return " ".join(f"{size}:{rules[size]}" for size in Size if size in rules) or "-"


def render_roles(config: FugueConfig, size: Size | None = None) -> str:
    """The role table; with a Size it shows the tier and models that Size resolves to."""
    rows = []
    for role, role_config in config.roles.items():
        if size is None:
            tier, model = _policy_text(config, role), "-"
        else:
            choice = resolve_models(config, role, size)
            tier = "-" if choice.tier is None else str(choice.tier)
            model = describe_models(choice.models)
        rows.append(
            [
                str(role),
                " > ".join(
                    [role_config.provider, *(entry.provider for entry in role_config.fallback)]
                ),
                str(role_config.authentication_mode),
                tier,
                model,
            ]
        )
    lines = _table(["role", "provider", "authentication mode", "tier", "model"], rows)
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
    hint = next_step(outcome)
    if hint is not None:
        lines.append(f"next: {hint}")
    return "\n".join(lines)


_OUTCOME_HINT_STATES = frozenset(
    {
        RunState.WAITING_FOR_CI,
        RunState.WAITING_FOR_DECISION,
        RunState.WAITING_FOR_MERGE_APPROVAL,
        RunState.INTERRUPTED,
        RunState.PROVIDER_LIMITED,
    }
)


def next_step_for(state: str, run_id: int, ticket_number: int) -> str | None:
    """Return the exact command that continues a run in this state, if any."""
    resume = f"fugue resume {run_id}"
    hints: dict[str, str] = {
        RunState.WAITING_FOR_CI: f"fugue review {run_id}",
        RunState.WAITING_FOR_DECISION: (
            f"fugue decide {run_id} --accept INDEX --reject 'INDEX:reason text'"
        ),
        RunState.WAITING_FOR_MERGE_APPROVAL: (
            f"fugue request-approval {run_id}; then fugue approve {run_id} --reason 'reason text'"
        ),
        RunState.WAITING_FOR_SELECTION: f"fugue question {ticket_number}",
        RunState.INTERRUPTED: resume,
        RunState.PROVIDER_LIMITED: resume,
        RunState.RUNNING: resume,
        RunState.CHECKING: resume,
        RunState.REPAIRING: resume,
        RunState.BLOCKED: resume,
        RunState.FAILED: resume,
        "created": resume,
    }
    return hints.get(state)


def next_step(outcome: RunOutcome) -> str | None:
    """Return the command that continues a run in this state, if any."""
    if outcome.state not in _OUTCOME_HINT_STATES:
        return None
    return next_step_for(outcome.state, outcome.run_id, 0)


@dataclass(frozen=True)
class BatchRow:
    ticket: int
    state: str
    run_id: int | None
    detail: str
    next_step: str | None = None


def render_skip(ticket: int, blockers: Sequence[int]) -> str:
    return f"skipped: blocked by {', '.join(f'#{number}' for number in blockers)}"


def render_batch_summary(rows: Sequence[BatchRow]) -> str:
    table = _table(
        ["ticket", "state", "run", "result"],
        [
            [
                f"#{row.ticket}",
                row.state,
                "-" if row.run_id is None else str(row.run_id),
                row.detail,
            ]
            for row in rows
        ],
    )
    hints = [f"#{row.ticket} next: {row.next_step}" for row in rows if row.next_step]
    return "\n".join([*table, *hints])


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
    lines += _section(
        "models",
        ["role", "size", "tier", "source", "model"],
        [[m.role, m.size, m.tier, m.source, m.models] for m in detail.models],
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
    def is_table_list(value: object) -> bool:
        return (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, dict) for item in value)
        )

    scalars = {
        key: value
        for key, value in table.items()
        if not isinstance(value, dict) and not is_table_list(value)
    }
    tables = {key: value for key, value in table.items() if isinstance(value, dict)}
    table_lists = {key: value for key, value in table.items() if is_table_list(value)}
    lines = [f"{key} = {_toml_scalar(value)}" for key, value in scalars.items()]
    for key, value in tables.items():
        child = [*path, key]
        lines.extend(["", f"[{'.'.join(child)}]", *_toml_lines(child, value)])
    for key, items in table_lists.items():
        child = [*path, key]
        for item in items:
            lines.extend(["", f"[[{'.'.join(child)}]]", *_toml_lines(child, item)])
    return lines


def render_config(config: FugueConfig) -> str:
    """Render the effective configuration; it holds no secrets."""
    data = config.model_dump(mode="json", exclude_none=True)
    return "\n".join(_toml_lines([], data)).strip()


@dataclass(frozen=True)
class OverviewSection:
    """One printable part of the repository overview: plain lines, a table or a panel."""

    kind: Literal["lines", "table", "panel"]
    title: str = ""
    header: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    lines: tuple[str, ...] = ()


def _table_section(
    title: str, header: Sequence[str], rows: Sequence[Sequence[str]]
) -> OverviewSection:
    if not rows:
        return OverviewSection("lines", lines=(f"{title}: none",))
    return OverviewSection("table", title, tuple(header), tuple(tuple(row) for row in rows))


def _usage_line(usage: UsageRecord) -> str:
    return (
        "usage (reported values, not a bill): "
        f"input {_usage_value(usage.input_tokens)}, "
        f"cached input {_usage_value(usage.cached_input_tokens)}, "
        f"output {_usage_value(usage.output_tokens)}, "
        f"reasoning {_usage_value(usage.reasoning_tokens)}, "
        f"cost estimate USD {_usage_value(usage.cost_estimate_usd)}, "
        f"duration ms {_usage_value(usage.duration_ms)}"
    )


def _overview_notes(overview: Overview, summary: SummaryResult | None) -> list[str]:
    notes: list[str] = []
    if overview.offline is not None:
        notes.append(f"note: GitHub unreachable ({overview.offline}); showing local runs only")
    if overview.truncated:
        notes.append(f"note: showing {overview.limit} of more than {overview.limit} open tickets")
    notes.extend(f"note: could not read #{n}: {message}" for n, message in overview.unreadable)
    if summary is not None:
        if summary.skipped is not None:
            notes.append(f"note: summary skipped ({summary.skipped})")
        if summary.note is not None:
            notes.append(f"note: {summary.note}")
    return notes


def render_overview(overview: Overview, summary: SummaryResult | None) -> list[OverviewSection]:
    """The repository overview as ordered sections; empty sections read `<title>: none`."""
    header = [
        f"repository: {overview.repo}",
        f"generated: {overview.generated_at.isoformat(timespec='seconds')}",
        *_overview_notes(overview, summary),
    ]
    sections = [OverviewSection("lines", lines=tuple(header))]
    if summary is not None and summary.text is not None:
        body = summary.text
        if summary.usage is not None:
            body = f"{body}\n{_usage_line(summary.usage)}"
        sections.append(
            OverviewSection(
                "panel", "summary (cached)" if summary.cached else "summary", lines=(body,)
            )
        )
    if not overview.tickets:
        sections.append(OverviewSection("lines", lines=("open tickets by Status: none",)))
    for status in Status:
        rows = [
            [f"#{t.number}", t.issue_type, t.size, t.priority, t.title]
            for t in overview.tickets
            if t.status == status
        ]
        if rows:
            sections.append(
                _table_section(
                    f"open tickets, {status} ({len(rows)})",
                    ["ticket", "type", "size", "priority", "title"],
                    rows,
                )
            )
    sections.append(
        _table_section(
            "Epic and Question progress (closed/total)",
            ["ticket", "type", "progress", "title"],
            [
                [f"#{p.number}", p.issue_type, f"{p.closed}/{p.total}", p.title]
                for p in overview.progress
            ],
        )
    )
    sections.append(
        _table_section(
            "ready to start",
            ["ticket", "size", "title", "next"],
            [[f"#{r.number}", r.size, r.title, f"fugue run {r.number}"] for r in overview.ready],
        )
    )
    sections.append(
        _table_section(
            "blocked tickets",
            ["ticket", "title", "blocked by"],
            [
                [f"#{b.number}", b.title, "blocked by " + ", ".join(f"#{n}" for n in b.blockers)]
                for b in overview.blocked
            ],
        )
    )
    waiting = [
        [f"#{w.number}", "-", w.title, f"fugue question {w.number}"] for w in overview.waiting
    ]
    waiting += [
        [
            f"#{r.ticket_number}",
            str(r.id),
            r.state,
            next_step_for(r.state, r.id, r.ticket_number) or "-",
        ]
        for r in overview.runs
        if r.state in WAITING_RUN_STATES
    ]
    sections.append(
        _table_section("waiting for a decision", ["ticket", "run", "what", "next"], waiting)
    )
    sections.append(
        _table_section(
            "open pull requests",
            ["id", "title", "ci (pass/fail/pending)", "review", "approved", "head"],
            [
                [
                    f"#{p.number}",
                    p.title,
                    f"{p.passing}/{p.failing}/{p.pending}",
                    p.review_decision,
                    "yes" if p.approved else "no",
                    p.head_sha[:7],
                ]
                for p in overview.pull_requests
            ],
        )
    )
    sections.append(
        _table_section(
            "unfinished runs",
            ["run", "ticket", "state", "since last activity", "next"],
            [
                [
                    str(r.id),
                    f"#{r.ticket_number}",
                    r.state,
                    format_elapsed(r.age_seconds),
                    "next: " + (next_step_for(r.state, r.id, r.ticket_number) or "-"),
                ]
                for r in overview.runs
            ],
        )
    )
    sections.append(
        _table_section(
            "recently merged pull requests",
            ["id", "title", "merged"],
            [[f"#{m.number}", m.title, m.merged_at] for m in overview.merged],
        )
    )
    sections.append(
        _table_section(
            "recently closed tickets",
            ["ticket", "title"],
            [[f"#{c.number}", c.title] for c in overview.closed],
        )
    )
    return sections
