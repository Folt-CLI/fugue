"""The `overview` command: gather, optionally summarise, and print. It changes nothing."""

import asyncio
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fugue.agents import (
    AdapterConfigError,
    AdapterRegistry,
    ProviderAdapter,
    UnknownProvider,
    register_claude,
    register_codex,
)
from fugue.cli import render, terminal, wiring
from fugue.config import FugueConfig
from fugue.github.client import GitHubError
from fugue.github.overview import OverviewGitHubClient
from fugue.models import Role
from fugue.overview import (
    Overview,
    OverviewPort,
    SummaryResult,
    gather,
    offline_overview,
    summarise,
)

GithubFactory = Callable[[FugueConfig, str], OverviewPort]
AdapterFactory = Callable[[FugueConfig], ProviderAdapter | None]


def _require_project(config: FugueConfig) -> tuple[str, int]:
    github = config.github
    if github.project_owner is None or github.project_number is None:
        raise wiring.WiringError("github.project_owner and github.project_number are required")
    return github.project_owner, github.project_number


def build_github(config: FugueConfig, repo: str) -> OverviewPort:
    owner, number = _require_project(config)
    return OverviewGitHubClient(
        repo, owner, number, resolve_token_env=config.github.resolve_token_env
    )


def build_adapter(config: FugueConfig) -> ProviderAdapter | None:
    registry = AdapterRegistry()
    register_claude(registry)
    register_codex(registry)
    role_config = config.roles.get(Role.ANALYST)
    if role_config is None:
        return None
    return registry.build_for_role(
        role_config,
        clock=lambda: datetime.now(UTC),
        cooldown_seconds=config.limits.provider_cooldown_seconds,
    )


# Tests replace these hooks to inject fake GitHub data and a fake analyst.
github_factory: GithubFactory = build_github
adapter_factory: AdapterFactory = build_adapter


def _reason(error: BaseException) -> str:
    text = error.stderr_tail if isinstance(error, GitHubError) else str(error)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    reason = lines[-1] if lines else type(error).__name__
    return reason if len(reason) <= 120 else reason[:117] + "..."


def _print_sections(sections: list[render.OverviewSection]) -> None:
    for index, section in enumerate(sections):
        if index:
            terminal.print_text("")
        if section.kind == "table":
            terminal.print_table(section.header, section.rows, title=f"{section.title}:")
        elif section.kind == "panel":
            terminal.print_panel(section.title, "\n".join(section.lines))
        else:
            for line in section.lines:
                terminal.print_text(line)


def _summary(
    overview: Overview,
    config: FugueConfig,
    cache_path: Path,
    now: datetime,
    refresh: bool,
) -> SummaryResult:
    try:
        adapter = adapter_factory(config)
    except (UnknownProvider, AdapterConfigError) as error:
        return SummaryResult(skipped=f"analyst adapter unavailable: {error}")
    if adapter is None:
        return SummaryResult(skipped="no analyst role configured")
    return asyncio.run(
        summarise(
            overview,
            adapter=adapter,
            config=config,
            cache_path=cache_path,
            now=now,
            refresh=refresh,
        )
    )


def run_overview(
    config: FugueConfig,
    root: Path,
    *,
    repo: str | None = None,
    summary: bool = True,
    refresh: bool = False,
    now: datetime | None = None,
) -> int:
    """Print the repository overview and return the exit code."""
    _require_project(config)
    repo = repo if repo is not None else wiring.resolve_repo(config, root)
    moment = now if now is not None else datetime.now(UTC)
    state_dir = wiring.resolve_state_dir(config, os.environ, Path.home())
    store = wiring.open_store(state_dir, config.limits.database_wait_seconds)
    try:
        runs = store.list_runs(repo=repo)
        approvals = store.merge_approvals(repo)
    finally:
        store.close()
    port = github_factory(config, repo)
    try:
        overview = asyncio.run(
            gather(
                port,
                runs,
                approvals,
                repo=repo,
                limit=config.limits.overview_max_tickets,
                now=moment,
                policy=config.policy,
            )
        )
    except (GitHubError, OSError, TimeoutError) as error:
        overview = offline_overview(runs, repo=repo, now=moment, reason=_reason(error))
    result: SummaryResult | None = None
    if summary and overview.offline is None:
        owner, name = repo.split("/", 1)
        cache_path = state_dir / f"overview-{owner}-{name}.json"
        result = _summary(overview, config, cache_path, moment, refresh)
    _print_sections(render.render_overview(overview, result))
    return 0
