"""Settings of the work flow."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from fugue.core.flow import FlowConfig


class WorkConfig(FlowConfig):
    """FUGUE_WORK_STARTED_STATUS: the project status a ticket moves to when work starts.
    FUGUE_WORK_STATUS_FIELD: the name of the project field that holds the status.
    FUGUE_WORK_WORKTREE_ROOT: the folder worktrees go in; default `.worktrees` in the
    repository, one folder per branch (`.worktrees/feature/1-title`).
    FUGUE_WORK_BASE_BRANCH: the branch work starts from; default the repository's default.
    FUGUE_WORK_SLUG_LENGTH: the longest slug in a branch or folder name.
    FUGUE_WORK_GATE: the gate: a name and a command for each check, run in the worktree in
    order, as a table (`[work.gate]` in a settings file, or JSON in the variable).
    FUGUE_WORK_GATE_TIMEOUT: the longest a single check may run, in seconds.
    FUGUE_WORK_AUTO, FUGUE_WORK_RECORD_RUNS and FUGUE_WORK_RUNS_DIR: see FlowConfig.

    Each can also be set with `fugue config set work.<name> <value>`; `base_branch` belongs to
    one repository and is never taken from the global file."""

    model_config = SettingsConfigDict(env_prefix="FUGUE_WORK_")

    section = "work"
    local_only = frozenset({"base_branch"})

    started_status: str = "S3: In Implementation"
    status_field: str = "Status"
    worktree_root: Path | None = None
    base_branch: str | None = None
    slug_length: int = 40
    gate: dict[str, str] = Field(default_factory=dict)
    gate_timeout: float = 1800.0
