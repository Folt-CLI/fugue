"""Build provider adapters from validated role configuration."""

from collections.abc import Callable

from fugue.agents.base import ProviderAdapter
from fugue.config import RoleConfig


class UnknownProvider(ValueError):
    """No factory is registered for the configured provider."""


class AdapterConfigError(ValueError):
    """A provider adapter disagrees with its role configuration."""


class AdapterRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[RoleConfig], ProviderAdapter]] = {}

    def register(self, provider: str, factory: Callable[[RoleConfig], ProviderAdapter]) -> None:
        self._factories[provider] = factory

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
