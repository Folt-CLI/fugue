"""Resolve defaults and build the engine from configuration."""

import json
import re
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

from fugue.agents import AdapterRegistry, register_claude, register_codex
from fugue.artifacts import ArtifactStore
from fugue.cli.render import (
    ApprovalLine,
    AttemptLine,
    ModelLine,
    OperationLine,
    RunDetail,
    describe_models,
)
from fugue.config import FugueConfig
from fugue.engine import Engine, EngineDeps
from fugue.github import GitHubClient
from fugue.reviewer_app.store import config_dir
from fugue.reviewer_app.token import ReviewerTokenProvider
from fugue.store import Store

_REMOTE = re.compile(
    r"^(?:https://(?:[^/@]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)"
    r"(?P<owner>[^/\s]+)/(?P<name>[^/\s]+?)(?:\.git)?/?$"
)


class WiringError(ValueError):
    """The command cannot be wired from the environment or configuration."""


def _git_output(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
        )
    except OSError as error:
        raise WiringError(f"Cannot run git: {error}") from error
    if result.returncode != 0:
        raise WiringError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def resolve_repo_root(repo_root: Path | None, cwd: Path) -> Path:
    if repo_root is not None:
        return repo_root.resolve()
    try:
        return Path(_git_output(cwd, "rev-parse", "--show-toplevel")).resolve()
    except WiringError as error:
        raise WiringError(f"Not in a Git repository: {cwd}") from error


def parse_remote(url: str) -> str:
    """Return owner/name from a GitHub https or ssh remote URL."""
    match = _REMOTE.match(url.strip())
    if match is None:
        raise WiringError(f"Unsupported remote URL: {url}")
    return f"{match['owner']}/{match['name']}"


def resolve_repo(config: FugueConfig, repo_root: Path) -> str:
    if config.github.repo is not None:
        return config.github.repo
    return parse_remote(_git_output(repo_root, "remote", "get-url", "origin"))


def default_worktrees_root(repo_root: Path, repo: str) -> Path:
    return repo_root.parent / f"{repo.split('/')[-1]}-worktrees"


def resolve_state_dir(config: FugueConfig, env: Mapping[str, str], home: Path) -> Path:
    if config.state_dir is not None:
        return config.state_dir
    base = env.get("XDG_STATE_HOME")
    return (Path(base) if base else home / ".local" / "state") / "fugue"


def discover_config(config_path: Path | None, repo_root: Path) -> Path | None:
    if config_path is not None:
        return config_path
    candidate = repo_root / "fugue.toml"
    return candidate if candidate.is_file() else None


def reviewer_token(
    config: FugueConfig, repo: str, env: Mapping[str, str], home: Path
) -> Callable[[], Awaitable[str]] | None:
    """Use the saved reviewer app only when a separate reviewer identity is enabled."""
    if not config.github.reviewer_identity_separate:
        return None
    return ReviewerTokenProvider(repo, config_dir(env, home)).token


def build_engine(
    config: FugueConfig,
    repo_root: Path,
    *,
    env: Mapping[str, str],
    home: Path,
    progress: Callable[[str], None] | None = None,
) -> Engine:
    github = config.github
    if github.project_owner is None or github.project_number is None:
        raise WiringError("github.project_owner and github.project_number are required")
    repo = resolve_repo(config, repo_root)
    state_dir = resolve_state_dir(config, env, home)
    state_dir.mkdir(parents=True, exist_ok=True)
    registry = AdapterRegistry()
    register_codex(registry)
    register_claude(registry)
    worktrees_root = config.worktrees_root or default_worktrees_root(repo_root, repo)
    return Engine(
        EngineDeps(
            config=config,
            store=Store(
                state_dir / "fugue.db", busy_timeout_seconds=config.limits.database_wait_seconds
            ),
            artifacts=ArtifactStore(state_dir / "artifacts"),
            github=GitHubClient(
                repo,
                github.project_owner,
                github.project_number,
                resolve_token_env=github.resolve_token_env,
                review_token=reviewer_token(config, repo, env, home),
            ),
            registry=registry,
            repo_root=repo_root,
            worktrees_root=worktrees_root,
            state_dir=state_dir,
            progress=progress,
        )
    )


def open_store(state_dir: Path, wait_seconds: float = 30.0) -> Store:
    """Open the run store for reading; a missing store reads as empty and is not created."""
    path = state_dir / "fugue.db"
    return Store(path, busy_timeout_seconds=wait_seconds) if path.is_file() else Store(":memory:")


def load_run_detail(store: Store, run_id: int) -> RunDetail:
    # Store has no public readers for these records; read only the displayed columns.
    connection = store._connection
    return RunDetail(
        run=store.get_run(run_id),
        attempts=tuple(
            AttemptLine(row["stage"], row["attempt"], row["status"])
            for row in connection.execute(
                "SELECT stage, attempt, status FROM stage_attempts WHERE run_id = ? ORDER BY id",
                (run_id,),
            )
        ),
        operations=tuple(
            OperationLine(row["kind"], row["key"], row["status"])
            for row in connection.execute(
                "SELECT kind, key, status FROM operations WHERE run_id = ? ORDER BY id", (run_id,)
            )
        ),
        approvals=tuple(
            ApprovalLine(
                row["kind"],
                row["subject"],
                row["head_sha"],
                row["decision"],
                row["invalidated_at"] is None,
            )
            for row in connection.execute(
                "SELECT kind, subject, head_sha, decision, invalidated_at FROM approvals "
                "WHERE run_id = ? ORDER BY id",
                (run_id,),
            )
        ),
        usage=store.usage_summary(run_id),
        models=tuple(
            _model_line(row["key"], row["result_json"])
            for row in connection.execute(
                "SELECT key, result_json FROM operations "
                "WHERE run_id = ? AND kind = 'model_choice' AND result_json IS NOT NULL "
                "ORDER BY id",
                (run_id,),
            )
        ),
    )


def _model_line(role: str, result_json: str) -> ModelLine:
    data = json.loads(result_json)
    return ModelLine(
        role=role,
        size=str(data["size"]),
        tier="-" if data["tier"] is None else str(data["tier"]),
        source=str(data["source"]),
        models=describe_models(data["models"]),
    )
