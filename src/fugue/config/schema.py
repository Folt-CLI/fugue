"""Validated repository policy and local configuration."""

from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from fugue.models import AuthenticationMode, Role, Size, SizeRule


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_repair_attempts: int = Field(default=3, gt=0)
    max_concurrent_runs: int = Field(default=1, gt=0)
    database_wait_seconds: int = Field(default=30, gt=0)
    agent_timeout_seconds: int = Field(default=3600, gt=0)
    max_output_bytes: int = Field(default=65536, gt=0)
    ci_poll_interval_seconds: int = Field(default=20, gt=0)
    ci_timeout_seconds: int = Field(default=3600, gt=0)
    provider_cooldown_seconds: int = Field(default=300, gt=0)
    infrastructure_markers: list[str] = Field(
        default_factory=lambda: [
            "runner lost",
            "the runner has received a shutdown signal",
            "econnreset",
            "temporary failure in name resolution",
            "503 service unavailable",
            "no space left on device",
        ]
    )

    @field_validator("infrastructure_markers")
    @classmethod
    def normalize_markers(cls, value: list[str]) -> list[str]:
        markers = [item.strip().casefold() for item in value]
        if any(not item for item in markers):
            raise ValueError("infrastructure markers must not be empty")
        return markers


class ProviderEntry(BaseModel):
    """One provider tried after the role's own provider is unavailable."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "codex"]
    model: str | None = None
    authentication_mode: AuthenticationMode = AuthenticationMode.SUBSCRIPTION_LOGIN

    @field_validator("authentication_mode")
    @classmethod
    def forbid_api_key(cls, value: AuthenticationMode) -> AuthenticationMode:
        if value == AuthenticationMode.API_KEY:
            raise ValueError("provider fallback never uses api_key")
        return value


class RoleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "codex"]
    model: str | None = None
    authentication_mode: AuthenticationMode = AuthenticationMode.SUBSCRIPTION_LOGIN
    fallback: list[ProviderEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_distinct_providers(self) -> Self:
        providers = [self.provider, *(entry.provider for entry in self.fallback)]
        if len(set(providers)) != len(providers):
            raise ValueError("a provider must not repeat within one role")
        return self

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
    # False is single-account mode: GitHub forbids author self-approval, so reviews are COMMENT.
    reviewer_identity_separate: bool = False
    # Environment variable holding the token used to resolve review threads; None uses the
    # same environment as every other call.
    resolve_token_env: str | None = None

    @field_validator("resolve_token_env")
    @classmethod
    def require_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("resolve_token_env must not be empty")
        return value.strip()


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
