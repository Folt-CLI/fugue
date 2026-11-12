"""Validated repository policy and local configuration."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from fugue.models import AuthenticationMode, Role, Size, SizeRule


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_repair_attempts: int = Field(default=3, gt=0)
    max_concurrent_runs: int = Field(default=1, gt=0)
    agent_timeout_seconds: int = Field(default=3600, gt=0)
    max_output_bytes: int = Field(default=65536, gt=0)


class RoleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "codex"]
    model: str | None = None
    authentication_mode: AuthenticationMode = AuthenticationMode.SUBSCRIPTION_LOGIN

    @field_validator("authentication_mode")
    @classmethod
    def require_explicit_api_key(
        cls, value: AuthenticationMode, info: ValidationInfo
    ) -> AuthenticationMode:
        if value == AuthenticationMode.API_KEY and (
            not isinstance(info.context, dict) or info.context.get("from_toml") is not True
        ):
            raise ValueError("api_key must be set explicitly in TOML")
        return value


def _default_size_rules() -> dict[Size, SizeRule]:
    return {
        Size.XS: SizeRule(analysis=False, review=False, max_components=1),
        Size.S: SizeRule(analysis=False, review=True, max_components=1),
        Size.M: SizeRule(analysis=True, review=True, max_components=2),
        Size.L: SizeRule(analysis=True, review=True, max_components=None),
        Size.XL: SizeRule(analysis=False, review=False, max_components=0),
    }


def _default_gate_commands() -> list[list[str]]:
    return [
        ["uv", "run", "ruff", "format", "--check", "."],
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "mypy"],
        ["uv", "run", "pytest"],
        ["uv", "build"],
    ]


class RepoPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size_rules: dict[Size, SizeRule] = Field(default_factory=_default_size_rules)
    commit_subject_max: int = Field(default=72, gt=0)
    gate_commands: list[list[str]] = Field(default_factory=_default_gate_commands)
    autonomy_profile: Literal["human", "campaign"] = "human"

    @field_validator("size_rules", mode="before")
    @classmethod
    def merge_default_size_rules(cls, value: object) -> object:
        if isinstance(value, dict):
            return {**_default_size_rules(), **value}
        return value


def _default_roles() -> dict[Role, RoleConfig]:
    return {
        Role.ANALYST: RoleConfig(provider="claude"),
        Role.IMPLEMENTER: RoleConfig(provider="codex"),
        Role.REVIEWER: RoleConfig(provider="codex"),
    }


class GitHubConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str | None = None
    project_owner: str | None = None
    project_number: int | None = Field(default=None, gt=0)


class FugueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: RepoPolicy = Field(default_factory=RepoPolicy)
    limits: Limits = Field(default_factory=Limits)
    roles: dict[Role, RoleConfig] = Field(default_factory=_default_roles)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    worktrees_root: Path | None = None
    state_dir: Path | None = None

    @field_validator("roles", mode="before")
    @classmethod
    def merge_default_roles(cls, value: object) -> object:
        if isinstance(value, dict):
            return {**_default_roles(), **value}
        return value
