"""Validated repository policy and local configuration."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationInfo,
    field_validator,
    model_validator,
)

from fugue.models import AuthenticationMode, ModelTier, Role, Size, SizeRule


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interview_max_turns: int = Field(default=10, gt=0)
    interview_call_timeout_seconds: int = Field(default=300, gt=0)
    interview_automatic_max_seconds: int = Field(default=1800, gt=0)
    max_repair_attempts: int = Field(default=3, gt=0)
    max_concurrent_runs: int = Field(default=1, gt=0)
    database_wait_seconds: int = Field(default=30, gt=0)
    agent_timeout_seconds: int = Field(default=3600, gt=0)
    max_output_bytes: int = Field(default=65536, gt=0)
    ci_poll_interval_seconds: int = Field(default=20, gt=0)
    ci_timeout_seconds: int = Field(default=3600, gt=0)
    provider_cooldown_seconds: int = Field(default=300, gt=0)
    overview_max_tickets: int = Field(default=100, gt=0)
    overview_cache_seconds: int = Field(default=300, gt=0)
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


def validate_model_identifier(value: str) -> str:
    """A model identifier is one non-empty token that cannot be read as a command-line option."""
    text = value.strip()
    if not text:
        raise ValueError("model identifier must not be empty")
    if any(character.isspace() for character in text):
        raise ValueError("model identifier must not contain whitespace")
    if text.startswith("-"):
        raise ValueError("model identifier must not start with '-'")
    return text


def _check_optional_identifier(value: str | None) -> str | None:
    return None if value is None else validate_model_identifier(value)


class TierModels(BaseModel):
    """The model identifier of every model tier for one provider."""

    model_config = ConfigDict(extra="forbid")

    small: str
    medium: str
    large: str

    @field_validator("small", "medium", "large")
    @classmethod
    def check_identifier(cls, value: str) -> str:
        return validate_model_identifier(value)

    def for_tier(self, tier: ModelTier) -> str:
        return str(getattr(self, tier.value))


class ProviderEntry(BaseModel):
    """One provider tried after the role's own provider is unavailable."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["claude", "codex"]
    model: str | None = None
    authentication_mode: AuthenticationMode = AuthenticationMode.SUBSCRIPTION_LOGIN

    @field_validator("model")
    @classmethod
    def check_model(cls, value: str | None) -> str | None:
        return _check_optional_identifier(value)

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
    tier: ModelTier | None = None
    authentication_mode: AuthenticationMode = AuthenticationMode.SUBSCRIPTION_LOGIN
    fallback: list[ProviderEntry] = Field(default_factory=list)

    @field_validator("model")
    @classmethod
    def check_model(cls, value: str | None) -> str | None:
        return _check_optional_identifier(value)

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


def _default_tier_rules() -> dict[Role, dict[Size, ModelTier]]:
    # XS and XL analyst rows and every XL row are absent: those Sizes run no such role.
    return {
        Role.STAND_IN: {Size.L: ModelTier.MEDIUM},
        Role.ANALYST: {Size.M: ModelTier.MEDIUM, Size.L: ModelTier.LARGE},
        Role.IMPLEMENTER: {
            Size.XS: ModelTier.SMALL,
            Size.S: ModelTier.SMALL,
            Size.M: ModelTier.MEDIUM,
            Size.L: ModelTier.LARGE,
        },
        Role.REVIEWER: {
            Size.S: ModelTier.MEDIUM,
            Size.M: ModelTier.LARGE,
            Size.L: ModelTier.LARGE,
        },
    }


class RepoPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size_rules: dict[Size, SizeRule] = Field(default_factory=_default_size_rules)
    commit_subject_max: int = Field(default=72, gt=0)
    gate_commands: list[list[str]] = Field(default_factory=_default_gate_commands)
    autonomy_profile: Literal["human", "campaign"] = "human"
    tier_rules: dict[Role, dict[Size, ModelTier]] = Field(default_factory=_default_tier_rules)

    @field_validator("tier_rules", mode="before")
    @classmethod
    def merge_default_tier_rules(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        merged = _default_tier_rules()
        for role, rules in value.items():
            key = Role(role) if isinstance(role, str) else role
            if isinstance(rules, dict):
                merged[key] = {
                    **merged.get(key, {}),
                    **{Size(k) if isinstance(k, str) else k: v for k, v in rules.items()},
                }
            else:
                merged[key] = rules
        return merged

    @field_validator("size_rules", mode="before")
    @classmethod
    def merge_default_size_rules(cls, value: object) -> object:
        if isinstance(value, dict):
            return {**_default_size_rules(), **value}
        return value


def _default_roles() -> dict[Role, RoleConfig]:
    return {
        Role.ANALYST: RoleConfig(provider="claude"),
        Role.STAND_IN: RoleConfig(provider="codex"),
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


@dataclass(frozen=True)
class RunOverrides:
    """Model overrides given for one run; never read from configuration files."""

    tier: ModelTier | None = None
    role_tiers: Mapping[Role, ModelTier] = field(default_factory=dict)
    role_models: Mapping[Role, str] = field(default_factory=dict)


class FugueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: RepoPolicy = Field(default_factory=RepoPolicy)
    limits: Limits = Field(default_factory=Limits)
    roles: dict[Role, RoleConfig] = Field(default_factory=_default_roles)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    worktrees_root: Path | None = None
    state_dir: Path | None = None
    model_tiers: dict[Literal["claude", "codex"], TierModels] = Field(default_factory=dict)
    _run_overrides: RunOverrides = PrivateAttr(default_factory=RunOverrides)

    @property
    def run_overrides(self) -> RunOverrides:
        return self._run_overrides

    def with_overrides(
        self,
        *,
        tier: ModelTier | None = None,
        role_tiers: Mapping[Role, ModelTier] | None = None,
        role_models: Mapping[Role, str] | None = None,
    ) -> Self:
        """A copy carrying model overrides for one run; this configuration is unchanged."""
        copy = self.model_copy(deep=True)
        copy._run_overrides = RunOverrides(
            tier=tier,
            role_tiers=dict(role_tiers or {}),
            role_models={
                role: validate_model_identifier(model)
                for role, model in (role_models or {}).items()
            },
        )
        return copy

    @field_validator("roles", mode="before")
    @classmethod
    def merge_default_roles(cls, value: object) -> object:
        if isinstance(value, dict):
            return {**_default_roles(), **value}
        return value
