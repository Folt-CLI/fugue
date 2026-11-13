"""Build provider adapters from validated role configuration."""

from collections.abc import Callable
from datetime import datetime

from fugue.agents.base import ProviderAdapter
from fugue.agents.fallback import FallbackAdapter, FallbackMember, ProviderSwitch
from fugue.config import RoleConfig


class UnknownProvider(ValueError):
    """No factory is registered for the configured provider."""


class AdapterConfigError(ValueError):
    """A provider adapter disagrees with its role configuration."""


class AdapterRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[RoleConfig], ProviderAdapter]] = {}
        self._cooldowns: dict[str, datetime] = {}

    def register(self, provider: str, factory: Callable[[RoleConfig], ProviderAdapter]) -> None:
        self._factories[provider] = factory

    def build_for_role(
        self,
        role_config: RoleConfig,
        *,
        clock: Callable[[], datetime],
        cooldown_seconds: int,
        on_switch: Callable[[ProviderSwitch], None] | None = None,
    ) -> ProviderAdapter:
        """Build the role's adapter; a fallback list wraps every member in one chain.

        The cool-down map lives on the registry so runs sharing it also share cool-down.
        """
        if not role_config.fallback:
            return self.build(role_config)
        members = [FallbackMember(self.build(role_config), role_config.model)]
        for entry in role_config.fallback:
            members.append(
                FallbackMember(
                    self.build(
                        RoleConfig(
                            provider=entry.provider,
                            model=entry.model,
                            authentication_mode=entry.authentication_mode,
                        )
                    ),
                    entry.model,
                )
            )
        return FallbackAdapter(
            members,
            cooldowns=self._cooldowns,
            clock=clock,
            cooldown_seconds=cooldown_seconds,
            on_switch=on_switch,
        )

    def build(self, role_config: RoleConfig) -> ProviderAdapter:
        try:
            factory = self._factories[role_config.provider]
        except KeyError as exc:
            raise UnknownProvider(f"Unknown provider: {role_config.provider}") from exc
        adapter = factory(role_config)
        if adapter.authentication_mode != role_config.authentication_mode:
            raise AdapterConfigError(
                f"Provider {role_config.provider!r} authentication mode "
                f"{adapter.authentication_mode.value!r} does not match configured "
                f"{role_config.authentication_mode.value!r}"
            )
        return adapter
