"""Settings of the work flow."""

from pathlib import Path

from pydantic_settings import SettingsConfigDict

from fugue.core.flow import FlowConfig


class WorkConfig(FlowConfig):
    """FUGUE_WORK_STARTED_STATUS: the project status a ticket moves to when work starts.
    FUGUE_WORK_STATUS_FIELD: the name of the project field that holds the status.
    FUGUE_WORK_WORKTREE_ROOT: the folder worktrees go in; default `<repo folder>-worktrees`
    next to the repository.
    FUGUE_WORK_BASE_BRANCH: the branch work starts from; default the repository's default.
    FUGUE_WORK_SLUG_LENGTH: the longest slug in a branch or folder name.
    FUGUE_WORK_RECORD_RUNS and FUGUE_WORK_RUNS_DIR: see FlowConfig."""

    model_config = SettingsConfigDict(env_prefix="FUGUE_WORK_")

    started_status: str = "S4: In Implementation"
    status_field: str = "Status"
    worktree_root: Path | None = None
    base_branch: str | None = None
    slug_length: int = 40
