"""Typer commands and exit-code mapping."""

import asyncio
import getpass
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Literal, Protocol

import typer
from rich.console import Console

from fugue import __version__
from fugue.cli import render, wiring
from fugue.config import ConfigError, FugueConfig, load_config
from fugue.engine import Engine, EngineDeps, RunOutcome
from fugue.engine.review import Decision
from fugue.gitops import Git
from fugue.store import Run, StoreError
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


class RunEngine(Protocol):
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

    async def run_to_pr(self, ticket_number: int) -> RunOutcome:
        return await self._engine.run_to_pr(ticket_number)

    async def run_to_green(self, ticket_number: int) -> RunOutcome:
        return await self._engine.run_to_green(ticket_number)

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


@app.command()
def run(
    ticket: int,
    config: ConfigOption = None,
    repo_root: RepoRootOption = None,
    to_green: Annotated[bool, typer.Option("--to-green", help="Wait for green CI.")] = False,
) -> None:
    """Run a ticket to a pull request."""
    try:
        root_path = wiring.resolve_repo_root(repo_root, Path.cwd())
        loaded = _load(config, root_path)
        engine = engine_factory(loaded, root_path, _print)
    except wiring.WiringError as error:
        raise _fail(str(error)) from error
    try:
        _print(render.render_roles(loaded))
        outcome = asyncio.run(engine.run_to_green(ticket) if to_green else engine.run_to_pr(ticket))
    except (KeyboardInterrupt, asyncio.CancelledError) as error:
        raise _fail("run interrupted", _STATE_EXIT_CODES[RunState.INTERRUPTED]) from error
    except Exception as error:
        raise _fail(str(error), _STATE_EXIT_CODES[RunState.FAILED]) from error
    finally:
        engine.close()
    _print(render.render_outcome(outcome))
    raise typer.Exit(_STATE_EXIT_CODES.get(outcome.state, 0))


@app.command()
def status(
    run_id: Annotated[int | None, typer.Argument()] = None,
    ticket: Annotated[
        int | None, typer.Option("--ticket", help="Only runs for this ticket.")
    ] = None,
    config: ConfigOption = None,
    repo_root: RepoRootOption = None,
) -> None:
    """List recent runs, or show one run."""
    loaded = _load(config, _lenient_root(repo_root))
    store = wiring.open_store(wiring.resolve_state_dir(loaded, os.environ, Path.home()))
    try:
        if run_id is None:
            _print(render.render_runs(list(reversed(store.list_runs(ticket)))[:20]))
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
    config: ConfigOption = None,
    repo_root: RepoRootOption = None,
) -> None:
    """Report usage records (estimates, not a bill)."""
    loaded = _load(config, _lenient_root(repo_root))
    store = wiring.open_store(wiring.resolve_state_dir(loaded, os.environ, Path.home()))
    try:
        _print(render.render_usage(store.usage_by(by, ticket_number=ticket), by))
    finally:
        store.close()


@app.command()
def timing(
    run_id: Annotated[int | None, typer.Argument()] = None,
    config: ConfigOption = None,
    repo_root: RepoRootOption = None,
) -> None:
    """Report stage attempt durations for all runs or one run."""
    loaded = _load(config, _lenient_root(repo_root))
    store = wiring.open_store(wiring.resolve_state_dir(loaded, os.environ, Path.home()))
    try:
        if run_id is not None:
            try:
                store.get_run(run_id)
            except StoreError as error:
                raise _fail(f"Unknown run id {run_id}") from error
        _print(render.render_timing(store.stage_durations(run_id)))
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
