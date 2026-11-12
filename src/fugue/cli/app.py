"""Typer commands and exit-code mapping."""

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Protocol

import typer
from rich.console import Console

from fugue import __version__
from fugue.cli import render, wiring
from fugue.config import ConfigError, FugueConfig, load_config
from fugue.engine import Engine, RunOutcome
from fugue.store import StoreError
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

    def close(self) -> None: ...


EngineFactory = Callable[[FugueConfig, Path, Callable[[str], None]], RunEngine]


class _StoreClosingEngine:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def run_to_pr(self, ticket_number: int) -> RunOutcome:
        return await self._engine.run_to_pr(ticket_number)

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
def run(ticket: int, config: ConfigOption = None, repo_root: RepoRootOption = None) -> None:
    """Run a ticket to a pull request."""
    try:
        root_path = wiring.resolve_repo_root(repo_root, Path.cwd())
        loaded = _load(config, root_path)
        engine = engine_factory(loaded, root_path, _print)
    except wiring.WiringError as error:
        raise _fail(str(error)) from error
    try:
        _print(render.render_roles(loaded))
        outcome = asyncio.run(engine.run_to_pr(ticket))
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
