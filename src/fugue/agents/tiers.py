"""Resolve the model tier and per-provider model identifiers for one role and Size."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from fugue.config import FugueConfig, TierModels
from fugue.models import ModelTier, Role, Size

ChoiceSource = Literal[
    "override-model", "override-tier", "role-model", "role-tier", "policy", "default"
]


@dataclass(frozen=True)
class ModelChoice:
    """The tier chosen for a role and Size, and the model identifier of each chain member.

    `models` is keyed by provider name; None means the provider's own default model.
    """

    role: Role
    size: Size
    tier: ModelTier | None
    source: ChoiceSource
    models: Mapping[str, str | None]

    def to_json(self) -> dict[str, object]:
        return {
            "size": str(self.size),
            "tier": None if self.tier is None else str(self.tier),
            "source": self.source,
            "models": dict(self.models),
        }

    @classmethod
    def from_json(cls, role: Role, data: Mapping[str, object]) -> "ModelChoice":
        tier = data["tier"]
        models = data["models"]
        assert isinstance(models, dict)
        return cls(
            role=role,
            size=Size(str(data["size"])),
            tier=None if tier is None else ModelTier(str(tier)),
            source=data["source"],  # type: ignore[arg-type]
            models=dict(models),
        )


def resolve_models(config: FugueConfig, role: Role, size: Size) -> ModelChoice:
    """Apply the precedence: run model, run tier, role model, role tier, policy, default.

    An explicit model applies to the role's own provider only; every other chain member
    resolves the same tier through its own provider table.
    """
    role_config = config.roles[role]
    overrides = config.run_overrides
    run_model = overrides.role_models.get(role)
    run_tier = overrides.role_tiers.get(role, overrides.tier)
    policy_tier = config.policy.tier_rules.get(role, {}).get(size)

    explicit: str | None = None
    if run_tier is not None:
        tier: ModelTier | None = run_tier
    elif role_config.tier is not None:
        tier = role_config.tier
    else:
        tier = policy_tier

    source: ChoiceSource
    if run_model is not None:
        explicit, source = run_model, "override-model"
    elif run_tier is not None:
        source = "override-tier"
    elif role_config.model is not None:
        explicit, source = role_config.model, "role-model"
    elif role_config.tier is not None:
        source = "role-tier"
    elif policy_tier is not None:
        source = "policy"
    else:
        source = "default"

    # Without any provider table no tier can be applied, so none is recorded.
    if not config.model_tiers:
        tier = None

    def from_table(provider: str) -> str | None:
        table: TierModels | None = config.model_tiers.get(provider)  # type: ignore[call-overload]
        if table is None or tier is None:
            return None
        return table.for_tier(tier)

    models: dict[str, str | None] = {
        role_config.provider: explicit if explicit is not None else from_table(role_config.provider)
    }
    for entry in role_config.fallback:
        models[entry.provider] = (
            entry.model if entry.model is not None else from_table(entry.provider)
        )
    return ModelChoice(role=role, size=size, tier=tier, source=source, models=models)
