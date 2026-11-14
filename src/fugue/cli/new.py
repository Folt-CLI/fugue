"""The `new` command: guided ticket creation with a saved draft."""

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
from fugue.cli import terminal, wiring
from fugue.cli.chat import default_reader
from fugue.config import FugueConfig
from fugue.github.creation import NewTicketGitHubClient
from fugue.models import Role
from fugue.tickets.draft import TicketDraft
from fugue.tickets.drafts import DraftStore
from fugue.tickets.session import NewPort, NewSession

GithubFactory = Callable[[FugueConfig, str], NewPort]
AdapterFactory = Callable[[FugueConfig], ProviderAdapter | None]


def _require_project(config: FugueConfig) -> tuple[str, int]:
    github = config.github
    if github.project_owner is None or github.project_number is None:
        raise wiring.WiringError("github.project_owner and github.project_number are required")
    return github.project_owner, github.project_number


def build_github(config: FugueConfig, repo: str) -> NewPort:
    owner, number = _require_project(config)
    return NewTicketGitHubClient(
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


# Tests replace these hooks to inject a fake GitHub client, a fake analyst and input.
github_factory: GithubFactory = build_github
adapter_factory: AdapterFactory = build_adapter
reader: Callable[[str], str] = default_reader


def build_session(
    config: FugueConfig,
    root: Path,
    *,
    repo: str,
    draft: TicketDraft,
    drafts: DraftStore,
) -> NewSession:
    try:
        adapter = adapter_factory(config)
        note = None
    except (UnknownProvider, AdapterConfigError) as error:
        adapter, note = None, f"note: analyst adapter unavailable: {error}"
    if note:
        terminal.print_text(note)
    state_dir = wiring.resolve_state_dir(config, os.environ, Path.home())
    store = wiring.open_store(state_dir, config.limits.database_wait_seconds)
    try:
        runs = store.list_runs(repo=repo)
        usage = list(store.usage_by("ticket", repo=repo))
    finally:
        store.close()
    return NewSession(
        github_factory(config, repo),
        drafts,
        root,
        draft,
        config=config,
        adapter=adapter,
        repo=repo,
        runs=runs,
        usage=usage,
        prompt=reader,
    )


def run_new(
    config: FugueConfig,
    root: Path,
    *,
    suggest: bool = False,
    resume: str | None = None,
    parent: int | None = None,
    assignee: str | None = None,
) -> int:
    """Run the guided session and return the exit code."""
    _require_project(config)
    wiring.resolve_repo_root(None, root)
    repo = wiring.resolve_repo(config, root)
    state_dir = wiring.resolve_state_dir(config, os.environ, Path.home())
    drafts = DraftStore(state_dir)
    if resume is not None:
        draft = drafts.load(resume)
        if not draft.repo:
            raise ValueError(f"Draft {resume} has no repository identity; cannot safely resume")
        if draft.repo.casefold() != repo.casefold():
            raise ValueError(f"Draft {resume} belongs to {draft.repo}, not {repo}; cannot resume")
    else:
        draft = TicketDraft(repo=repo, mode="suggest" if suggest else "idea")
    patch: dict[str, object] = {}
    if parent is not None:
        patch["parent"] = parent
    if assignee:
        patch["assignee"] = assignee
    if patch:
        draft.apply_patch(patch, "person")
    session = build_session(config, root, repo=repo, draft=draft, drafts=drafts)
    return asyncio.run(session.run())
