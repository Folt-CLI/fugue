"""Typer commands and exit-code mapping."""

import asyncio
import getpass
import json
import os
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Literal, Protocol

import typer
from rich.console import Console

from fugue import __version__
from fugue.analysis.stage import AnalysisGitHubClient, analyse
from fugue.cli import render, wiring
from fugue.config import ConfigError, FugueConfig, load_config
from fugue.engine import Engine, EngineDeps, RunOutcome
from fugue.engine.locks import TicketLock, TicketLocked, format_elapsed
from fugue.engine.review import Decision
from fugue.engine.scheduler import run_many
from fugue.github import GitHubClient
from fugue.gitops import Git
from fugue.models import ModelTier, Role, Size, Status, Ticket
from fugue.store import Run, Store, StoreError
from fugue.store.records import Approval
from fugue.workflow import RunState

EXIT_USAGE = 2
_STATE_EXIT_CODES: dict[str, int] = {
    RunState.BLOCKED: 3,
    RunState.FAILED: 4,
    RunState.PROVIDER_LIMITED: 5,
    RunState.INTERRUPTED: 6,
}

app = typer.Typer(name="fugue", no_args_is_help=True, add_completion=False)
config_app = typer.Typer(no_args_is_help=True)
app.add_typer(config_app, name="config", help="Inspect configuration.")

ConfigOption = Annotated[Path | None, typer.Option("--config", help="Path to fugue.toml.")]
RepoRootOption = Annotated[
    Path | None, typer.Option("--repo-root", help="Repository root (default: Git top level).")
]
RepoFilterOption = Annotated[
    str | None, typer.Option("--repo", help="Only runs in this repository (OWNER/NAME).")
]


class RunEngine(Protocol):
    @property
    def deps(self) -> EngineDeps: ...

    async def get_ticket(self, number: int) -> Ticket: ...

    async def run_to_pr(self, ticket_number: int) -> RunOutcome: ...

    async def run_to_green(self, ticket_number: int) -> RunOutcome: ...

    async def run_review(self, run_id: int) -> RunOutcome: ...

    async def decide(self, run_id: int, decisions: Mapping[int, Decision]) -> RunOutcome: ...

    async def request_approval(self, run_id: int) -> RunOutcome: ...

    def approve(self, run_id: int, decided_by: str, reason: str) -> Approval: ...

    async def merge_and_close(self, run_id: int) -> RunOutcome: ...

    async def resume(self, run_id: int) -> RunOutcome: ...

    def close(self) -> None: ...


EngineFactory = Callable[[FugueConfig, Path, Callable[[str], None]], RunEngine]


class _ResumeEngine(Engine):
    """Keep ticket-based engine continuation bound to the requested run."""

    def __init__(self, deps: EngineDeps, run_id: int) -> None:
        super().__init__(deps)
        self._resume_run_id = run_id

    def _find_or_create_run(self, ticket_number: int) -> Run:
        run = self.deps.store.get_run(self._resume_run_id)
        if run.ticket_number != ticket_number:
            raise ValueError("Resume ticket does not match the requested run")
        return run


class _StoreClosingEngine:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def deps(self) -> EngineDeps:
        return self._engine.deps

    async def get_ticket(self, number: int) -> Ticket:
        return await self._engine.deps.github.get_ticket(number)

    async def run_to_pr(self, ticket_number: int) -> RunOutcome:
        outcome = await self._analyse_if_needed(ticket_number)
        return outcome if outcome is not None else await self._engine.run_to_pr(ticket_number)

    async def run_to_green(self, ticket_number: int) -> RunOutcome:
        outcome = await self._analyse_if_needed(ticket_number)
        return outcome if outcome is not None else await self._engine.run_to_green(ticket_number)

    async def _analyse_if_needed(
        self, ticket_number: int, run_id: int | None = None
    ) -> RunOutcome | None:
        ticket = await self._engine.deps.github.get_ticket(ticket_number)
        if ticket.size in {Size.M, Size.L} and ticket.status in {Status.TO_DO, Status.IN_ANALYSIS}:
            return await analyse(self._engine.deps, ticket, run_id=run_id)
        return None

    async def run_review(self, run_id: int) -> RunOutcome:
        return await self._engine.run_review(run_id)

    async def decide(self, run_id: int, decisions: Mapping[int, Decision]) -> RunOutcome:
        return await self._engine.decide(run_id, decisions)

    async def request_approval(self, run_id: int) -> RunOutcome:
        return await self._engine.request_approval(run_id)

    def approve(self, run_id: int, decided_by: str, reason: str) -> Approval:
        return self._engine.approve(run_id, decided_by, reason)

    async def merge_and_close(self, run_id: int) -> RunOutcome:
        return await self._engine.merge_and_close(run_id)

    async def resume(self, run_id: int) -> RunOutcome:
        """Continue durable progress without supplying a decision or approval."""
        store = self._engine.deps.store
        run = store.get_run(run_id)
        if run.state == RunState.COMPLETED:
            return RunOutcome(run_id, RunState.COMPLETED)
        if (
            store._connection.execute(
                "SELECT 1 FROM stage_attempts WHERE run_id = ? AND stage = 'analyse' LIMIT 1",
                (run_id,),
            ).fetchone()
            is not None
        ):
            outcome = await self._analyse_if_needed(run.ticket_number, run_id)
            if outcome is not None:
                return outcome
        detail = wiring.load_run_detail(store, run_id)
        identity = self._engine._saved_identity(run_id)
        if identity is None or not identity.path.exists():
            # Closure can continue after removing the worktree; other stages
            # need preparation to observe and restore it before using Git there.
            if any(o.kind in {"merge", "closure"} for o in detail.operations):
                return await self.merge_and_close(run_id)
            return await _ResumeEngine(self._engine.deps, run_id).run_to_pr(run.ticket_number)
        # Reuse the durable decision intent after an interrupted repair or thread reply.
        pending = store._connection.execute(
            "SELECT intent_json FROM operations WHERE run_id = ? AND kind = 'decision' "
            "AND status != 'done' ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if pending is not None:
            saved = json.loads(pending[0])["decisions"]
            decisions = {int(index): Decision(**value) for index, value in saved.items()}
            return await self.decide(run_id, decisions)
        head = await Git(identity.path).head_sha()
        latest_review = store._connection.execute(
            "SELECT status, intent_json FROM operations WHERE run_id = ? AND kind = 'review' "
            "AND json_extract(intent_json, '$.head') = ? ORDER BY id DESC LIMIT 1",
            (run_id, head),
        ).fetchone()
        if latest_review is not None:
            review = json.loads(latest_review["intent_json"])
            decision = store._connection.execute(
                "SELECT status FROM operations WHERE run_id = ? AND kind = 'decision' AND key = ?",
                (run_id, f"round-{review['round']}"),
            ).fetchone()
            if latest_review["status"] != "done" or (
                review["findings"]
                and (decision is None or any(f["blocking"] for f in review["findings"]))
            ):
                return await self.run_review(run_id)
        if any(o.kind == "merge" and o.key == head for o in detail.operations) or any(
            a.kind == "merge" and a.valid and a.decision == "approved" and a.head_sha == head
            for a in detail.approvals
        ):
            return await self.merge_and_close(run_id)
        if any(
            o.kind in {"merge_request", "merge_gate"} and o.key == head for o in detail.operations
        ):
            return await self.request_approval(run_id)
        if run.state == RunState.WAITING_FOR_MERGE_APPROVAL:
            return await self.request_approval(run_id)
        if any(o.kind == "review" for o in detail.operations) or any(
            a.stage == "review" for a in detail.attempts
        ):
            return await self.run_review(run_id)
        return await _ResumeEngine(self._engine.deps, run_id).run_to_green(run.ticket_number)

    def close(self) -> None:
        self._engine.deps.store.close()


def _real_engine_factory(
    config: FugueConfig, repo_root: Path, progress: Callable[[str], None]
) -> RunEngine:
    engine = wiring.build_engine(
        config, repo_root, env=os.environ, home=Path.home(), progress=progress
    )
    github = engine.deps.github
    if isinstance(github, GitHubClient):
        engine.deps.github = AnalysisGitHubClient(
            github.repo,
            github.project_owner,
            github.project_number,
            runner=github.runner,
            strip_tokens=github.strip_tokens,
            field_ids=github.field_ids,
            gh_executable=github.gh_executable,
            max_output_bytes=github.max_output_bytes,
            project_field_limit=github.project_field_limit,
            resolve_token_env=github.resolve_token_env,
        )
    return _StoreClosingEngine(engine)


# Tests replace this hook to inject a fake engine.
engine_factory: EngineFactory = _real_engine_factory


def _print(text: str) -> None:
    Console().print(text, markup=False, highlight=False, soft_wrap=True)


def _fail(message: str, code: int = EXIT_USAGE) -> typer.Exit:
    Console(stderr=True).print(f"error: {message}", markup=False, highlight=False, soft_wrap=True)
    return typer.Exit(code)


def _version(value: bool) -> None:
    if value:
        _print(f"fugue {__version__}")
        raise typer.Exit


@app.callback()
def root(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version.")
    ] = False,
) -> None:
    """Drive a ticket to a pull request."""


def _load(config_path: Path | None, repo_root: Path) -> FugueConfig:
    try:
        return load_config(wiring.discover_config(config_path, repo_root))
    except ConfigError as error:
        raise _fail(str(error)) from error


def _lenient_root(repo_root: Path | None) -> Path:
    try:
        return wiring.resolve_repo_root(repo_root, Path.cwd())
    except wiring.WiringError:
        return Path.cwd()


@config_app.command("show")
def config_show(config: ConfigOption = None, repo_root: RepoRootOption = None) -> None:
    """Print the effective configuration."""
    _print(render.render_config(_load(config, _lenient_root(repo_root))))


def _open_store(config: FugueConfig) -> Store:
    return wiring.open_store(
        wiring.resolve_state_dir(config, os.environ, Path.home()),
        config.limits.database_wait_seconds,
    )


def _explain(error: BaseException, wait: int) -> str:
    """Describe an error for the user, naming the database wait when it applies."""
    if isinstance(error, sqlite3.OperationalError) and "locked" in str(error).lower():
        return (
            f"run database is locked by another fugue process; waited {wait} s "
            "(limits.database_wait_seconds)"
        )
    return str(error)


_BATCH_FAILED = "failed"
_BATCH_SKIPPED = "skipped"
_BATCH_LOCKED = "locked"


def _batch_exit_code(states: list[str]) -> int:
    """Highest precedence first: interrupted, failed, provider limited, then blocked-like."""
    if RunState.INTERRUPTED in states:
        return _STATE_EXIT_CODES[RunState.INTERRUPTED]
    if _BATCH_FAILED in states:
        return _STATE_EXIT_CODES[RunState.FAILED]
    if RunState.PROVIDER_LIMITED in states:
        return _STATE_EXIT_CODES[RunState.PROVIDER_LIMITED]
    if any(state in {RunState.BLOCKED, _BATCH_SKIPPED, _BATCH_LOCKED} for state in states):
        return _STATE_EXIT_CODES[RunState.BLOCKED]
    return 0


def _latest_run(engine: RunEngine, number: int) -> Run | None:
    try:
        runs = engine.deps.store.list_runs(number, engine.deps.github.repo)
    except Exception:
        return None
    return runs[-1] if runs else None


def _role_name(text: str, option: str) -> Role:
    try:
        return Role(text.strip())
    except ValueError:
        roles = ", ".join(str(role) for role in Role)
        raise _fail(f"{option}: unknown role {text!r}; use one of {roles}") from None


def _tier_name(text: str) -> ModelTier:
    try:
        return ModelTier(text.strip())
    except ValueError:
        tiers = ", ".join(str(item) for item in ModelTier)
        raise _fail(f"--tier: unknown tier {text!r}; use one of {tiers}") from None


def _parse_overrides(
    tier_args: list[str], model_args: list[str]
) -> tuple[ModelTier | None, dict[Role, ModelTier], dict[Role, str]]:
    """Parse --tier [ROLE=]TIER and --model ROLE=ID before any run starts."""
    all_tier: ModelTier | None = None
    role_tiers: dict[Role, ModelTier] = {}
    role_models: dict[Role, str] = {}
    for item in tier_args:
        name, separator, value = item.rpartition("=")
        if not separator:
            all_tier = _tier_name(item)
        else:
            role_tiers[_role_name(name, "--tier")] = _tier_name(value)
    for item in model_args:
        name, separator, value = item.partition("=")
        if not separator:
            raise _fail(f"--model: expected ROLE=ID, got {item!r}")
        role_models[_role_name(name, "--model")] = value
    return all_tier, role_tiers, role_models


def _run_single(loaded: FugueConfig, engine: RunEngine, ticket: int, to_green: bool) -> typer.Exit:
    wait = loaded.limits.database_wait_seconds
    try:
        with TicketLock(engine.deps.state_dir, engine.deps.github.repo, ticket) as lock:
            if lock.recovered is not None:
                _print(
                    f"note: recovered stale lock left by process {lock.recovered.pid} "
                    f"({format_elapsed(lock.recovered.age_seconds)} ago); resuming"
                )

            async def go() -> RunOutcome:
                # The tier depends on the ticket Size, so the table waits for the ticket.
                # A bare Engine has no get_ticket; read through its GitHub port instead. An
                # engine with neither shows the policy table, which needs no Size.
                reader = getattr(engine, "get_ticket", None) or getattr(
                    engine.deps.github, "get_ticket", None
                )
                if reader is None:
                    _print(render.render_roles(loaded))
                else:
                    found: Ticket = await reader(ticket)
                    _print(render.render_roles(loaded, found.size))
                return await (engine.run_to_green(ticket) if to_green else engine.run_to_pr(ticket))

            outcome = asyncio.run(go())
    except TicketLocked as error:
        raise _fail(str(error), _STATE_EXIT_CODES[RunState.BLOCKED]) from error
    except (KeyboardInterrupt, asyncio.CancelledError) as error:
        raise _fail("run interrupted", _STATE_EXIT_CODES[RunState.INTERRUPTED]) from error
    except Exception as error:
        raise _fail(_explain(error, wait), _STATE_EXIT_CODES[RunState.FAILED]) from error
    finally:
        engine.close()
    _print(render.render_outcome(outcome))
    return typer.Exit(_STATE_EXIT_CODES.get(outcome.state, 0))


def _run_batch(
    loaded: FugueConfig, root_path: Path, probe: RunEngine, numbers: list[int], to_green: bool
) -> typer.Exit:
    wait = loaded.limits.database_wait_seconds
    rows: dict[int, render.BatchRow] = {}
    engines: dict[int, RunEngine] = {}

    def labelled(number: int) -> Callable[[str], None]:
        return lambda line: _print(f"[#{number}] {line}")

    def factory(ticket: Ticket) -> RunEngine:
        engine = engine_factory(loaded, root_path, labelled(ticket.number))
        engines[ticket.number] = engine
        return engine

    def complete(ticket: Ticket, result: RunOutcome | Exception | None) -> None:
        number = ticket.number
        if result is None:
            message = render.render_skip(number, ticket.blocked_by)
            rows[number] = render.BatchRow(number, _BATCH_SKIPPED, None, message)
            _print(f"ticket #{number}\n{message}")
        elif isinstance(result, TicketLocked):
            rows[number] = render.BatchRow(number, _BATCH_LOCKED, None, str(result))
            _print(f"ticket #{number}\nerror: {result}")
        elif isinstance(result, Exception):
            message = _explain(result, wait)
            run = _latest_run(engines[number], number) if number in engines else None
            rows[number] = render.BatchRow(
                number, _BATCH_FAILED, None if run is None else run.id, f"error: {message}"
            )
            _print(f"ticket #{number}\nerror: {message}")
        else:
            detail = f"pull request #{result.pr_number}" if result.pr_number is not None else ""
            if not detail and result.problems:
                detail = result.problems[0]
            rows[number] = render.BatchRow(
                number, str(result.state), result.run_id, detail, render.next_step(result)
            )
            _print(f"ticket #{number}\n{render.render_outcome(result)}")

    async def execute() -> None:
        tickets: list[Ticket] = []
        try:
            for number in numbers:
                try:
                    found = await probe.get_ticket(number)
                    tickets.append(found)
                    _print(f"ticket #{number}\n{render.render_roles(loaded, found.size)}")
                except Exception as error:
                    message = _explain(error, wait)
                    rows[number] = render.BatchRow(number, _BATCH_FAILED, None, f"error: {message}")
                    _print(f"ticket #{number}\nerror: {message}")
        finally:
            probe.close()
        await run_many(
            tickets,
            factory,
            loaded.limits.max_concurrent_runs,
            until_green=to_green,
            on_complete=complete,
        )

    try:
        asyncio.run(execute())
    except (KeyboardInterrupt, asyncio.CancelledError):
        for number, engine in engines.items():
            run = _latest_run(engine, number)
            if number not in rows and run is not None and run.state == RunState.INTERRUPTED:
                _print(f"ticket #{number}: fugue resume {run.id}")
        raise _fail("run interrupted", _STATE_EXIT_CODES[RunState.INTERRUPTED]) from None
    finally:
        for engine in engines.values():
            engine.close()
    _print(render.render_batch_summary([rows[number] for number in numbers if number in rows]))
    return typer.Exit(_batch_exit_code([row.state for row in rows.values()]))


@app.command()
def run(
    tickets: Annotated[list[int], typer.Argument(help="Ticket numbers to run.")],
    config: ConfigOption = None,
    repo_root: RepoRootOption = None,
    to_green: Annotated[bool, typer.Option("--to-green", help="Wait for green CI.")] = False,
    tier: Annotated[
        list[str] | None,
        typer.Option(
            "--tier",
            help="Model tier for this run: [ROLE=]small|medium|large; repeatable.",
        ),
    ] = None,
    model: Annotated[
        list[str] | None,
        typer.Option("--model", help="Model identifier for one role: ROLE=ID; repeatable."),
    ] = None,
) -> None:
    """Run one or more tickets to a pull request."""
    if len(set(tickets)) != len(tickets):
        raise _fail("Duplicate ticket numbers")
    if any(number <= 0 for number in tickets):
        raise _fail("Ticket numbers must be positive")
    all_tier, role_tiers, role_models = _parse_overrides(tier or [], model or [])
    try:
        root_path = wiring.resolve_repo_root(repo_root, Path.cwd())
        loaded = _load(config, root_path)
        try:
            loaded = loaded.with_overrides(
                tier=all_tier, role_tiers=role_tiers, role_models=role_models
            )
        except ValueError as error:
            raise _fail(f"--model: {error}") from error
        engine = engine_factory(loaded, root_path, _print)
    except wiring.WiringError as error:
        raise _fail(str(error)) from error
    if len(tickets) == 1:
        raise _run_single(loaded, engine, tickets[0], to_green)
    raise _run_batch(loaded, root_path, engine, tickets, to_green)


@app.command()
def status(
    run_id: Annotated[int | None, typer.Argument()] = None,
    ticket: Annotated[
        int | None, typer.Option("--ticket", help="Only runs for this ticket.")
    ] = None,
    repo: RepoFilterOption = None,
    config: ConfigOption = None,
    repo_root: RepoRootOption = None,
) -> None:
    """List recent runs, or show one run."""
    loaded = _load(config, _lenient_root(repo_root))
    store = _open_store(loaded)
    try:
        if run_id is None:
            _print(render.render_runs(list(reversed(store.list_runs(ticket, repo)))[:20]))
            return
        try:
            detail = wiring.load_run_detail(store, run_id)
        except StoreError as error:
            raise _fail(f"Unknown run id {run_id}") from error
        if ticket is not None and detail.run.ticket_number != ticket:
            raise _fail(f"Run {run_id} is not for ticket #{ticket}")
        _print(render.render_run_detail(detail))
    finally:
        store.close()


@app.command()
def usage(
    by: Annotated[
        Literal["role", "provider", "run", "ticket"],
        typer.Option("--by", help="Group usage by role, provider, run or ticket."),
    ] = "role",
    ticket: Annotated[
        int | None, typer.Option("--ticket", help="Only runs for this ticket.")
    ] = None,
    repo: RepoFilterOption = None,
    config: ConfigOption = None,
    repo_root: RepoRootOption = None,
) -> None:
    """Report usage records (estimates, not a bill)."""
    loaded = _load(config, _lenient_root(repo_root))
    store = _open_store(loaded)
    try:
        _print(render.render_usage(store.usage_by(by, ticket_number=ticket, repo=repo), by))
    finally:
        store.close()


@app.command()
def timing(
    run_id: Annotated[int | None, typer.Argument()] = None,
    repo: RepoFilterOption = None,
    config: ConfigOption = None,
    repo_root: RepoRootOption = None,
) -> None:
    """Report stage attempt durations for all runs or one run."""
    loaded = _load(config, _lenient_root(repo_root))
    store = _open_store(loaded)
    try:
        if run_id is not None:
            try:
                store.get_run(run_id)
            except StoreError as error:
                raise _fail(f"Unknown run id {run_id}") from error
        _print(render.render_timing(store.stage_durations(run_id, repo)))
    finally:
        store.close()


def _execute(
    action: Callable[[RunEngine], RunOutcome | Approval],
    config: Path | None,
    repo_root: Path | None,
) -> None:
    try:
        root_path = wiring.resolve_repo_root(repo_root, Path.cwd())
        loaded = _load(config, root_path)
        engine = engine_factory(loaded, root_path, _print)
    except wiring.WiringError as error:
        raise _fail(str(error)) from error
    try:
        result = action(engine)
    except (KeyboardInterrupt, asyncio.CancelledError) as error:
        raise _fail("run interrupted", 6) from error
    except (ValueError, StoreError) as error:
        raise _fail(str(error)) from error
    except Exception as error:
        raise _fail(str(error), 4) from error
    finally:
        engine.close()
    if isinstance(result, Approval):
        _print(render.render_approval(result))
    else:
        _print(render.render_outcome(result))
        raise typer.Exit(_STATE_EXIT_CODES.get(result.state, 0))


@app.command()
def review(run_id: int, config: ConfigOption = None, repo_root: RepoRootOption = None) -> None:
    """Run a review of the pull request head."""
    _execute(lambda engine: asyncio.run(engine.run_review(run_id)), config, repo_root)


@app.command()
def decide(
    run_id: int,
    accept: Annotated[list[int] | None, typer.Option("--accept")] = None,
    reject: Annotated[list[str] | None, typer.Option("--reject", help="INDEX:reason text")] = None,
    config: ConfigOption = None,
    repo_root: RepoRootOption = None,
) -> None:
    """Accept findings or reject them with a reason."""
    decisions: dict[int, Decision] = {}

    def add(index: int, decision: Decision) -> None:
        if index <= 0:
            raise _fail("Finding index must be positive")
        if index in decisions:
            raise _fail(f"Duplicate decision for finding {index}")
        decisions[index] = decision

    for index in accept or []:
        add(index, Decision(True))
    for item in reject or []:
        index_text, separator, reason = item.partition(":")
        if not separator or not reason.strip():
            raise _fail("--reject requires INDEX:reason text")
        try:
            index = int(index_text)
        except ValueError as error:
            raise _fail("--reject requires a numeric finding index") from error
        add(index, Decision(False, reason.strip()))
    if not decisions:
        raise _fail("Provide at least one --accept or --reject decision")
    _execute(lambda engine: asyncio.run(engine.decide(run_id, decisions)), config, repo_root)


@app.command("request-approval")
def request_approval(
    run_id: int, config: ConfigOption = None, repo_root: RepoRootOption = None
) -> None:
    """Check the gate and request approval for the exact head SHA."""
    _execute(lambda engine: asyncio.run(engine.request_approval(run_id)), config, repo_root)


@app.command()
def approve(
    run_id: int,
    reason: Annotated[str, typer.Option("--reason", help="Reason for approval.")],
    by: Annotated[
        str | None, typer.Option("--by", help="Decision maker (default: OS user).")
    ] = None,
    config: ConfigOption = None,
    repo_root: RepoRootOption = None,
) -> None:
    """Record approval for the requested head SHA."""
    decided_by = getpass.getuser() if by is None else by
    if not decided_by.strip() or not reason.strip():
        raise _fail("Approval requires --by and --reason to be non-empty")
    _execute(lambda engine: engine.approve(run_id, decided_by, reason), config, repo_root)


@app.command()
def merge(run_id: int, config: ConfigOption = None, repo_root: RepoRootOption = None) -> None:
    """Merge the approved head and close the ticket."""
    _execute(lambda engine: asyncio.run(engine.merge_and_close(run_id)), config, repo_root)


@app.command()
def resume(run_id: int, config: ConfigOption = None, repo_root: RepoRootOption = None) -> None:
    """Continue a run from its last unfinished stage."""
    _execute(lambda engine: asyncio.run(engine.resume(run_id)), config, repo_root)


@app.command()
def question(
    ticket: int,
    select: Annotated[
        int | None, typer.Option("--select", help="Select an option directly.")
    ] = None,
    reason: Annotated[str | None, typer.Option("--reason", help="Reason for selection.")] = None,
    by: Annotated[
        str | None, typer.Option("--by", help="Decision maker (default: OS user).")
    ] = None,
    config: ConfigOption = None,
    repo_root: RepoRootOption = None,
) -> None:
    """Browse a Question and explicitly select its implementation scope."""
    from fugue.cli.question import build_question

    try:
        root_path = wiring.resolve_repo_root(repo_root, Path.cwd())
        loaded = _load(config, root_path)
        walkthrough = build_question(loaded, root_path)
        asyncio.run(walkthrough.run(ticket, select, reason=reason, by=by))
    except (KeyboardInterrupt, asyncio.CancelledError) as error:
        raise _fail("Question walkthrough interrupted", 6) from error
    except ValueError as error:
        raise _fail(str(error)) from error
    except Exception as error:
        raise _fail(str(error), 4) from error


@app.command("children")
def children(
    spec_file: Annotated[Path, typer.Argument(help="TOML child specifications.")],
    apply_changes: Annotated[
        bool, typer.Option("--apply", help="Apply the plan (default: dry run).")
    ] = False,
    config: ConfigOption = None,
    repo_root: RepoRootOption = None,
) -> None:
    """Plan or create children of a decided Question."""
    from fugue.children import apply, load_children, plan

    async def execute() -> None:
        root_path = wiring.resolve_repo_root(repo_root, Path.cwd())
        loaded = _load(config, root_path)
        spec = load_children(spec_file)
        github = loaded.github
        if github.project_owner is None or github.project_number is None:
            raise wiring.WiringError("github.project_owner and github.project_number are required")
        client = GitHubClient(
            wiring.resolve_repo(loaded, root_path), github.project_owner, github.project_number
        )
        planned = plan(spec, await client.get_ticket(spec.question), loaded.policy)
        if not apply_changes:
            for child, body in zip(spec.children, planned.bodies, strict=True):
                _print(f"{child.key}: {child.title} ({child.size})\n{body}")
            return
        state_dir = wiring.resolve_state_dir(loaded, os.environ, Path.home())
        state_dir.mkdir(parents=True, exist_ok=True)
        store = Store(state_dir / "fugue.db")
        try:
            result = await apply(planned, client, store)
            for key, number in result.items():
                _print(f"{key}: #{number}")
        finally:
            store.close()

    try:
        asyncio.run(execute())
    except (KeyboardInterrupt, asyncio.CancelledError) as error:
        raise _fail("Child creation interrupted", 6) from error
    except (ValueError, OSError, StoreError) as error:
        raise _fail(str(error)) from error
    except Exception as error:
        raise _fail(str(error), 4) from error
