"""Configuration, one class per concern, each read from the environment."""

from typing import Self

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from fugue.errors import ConfigError


class Config(BaseSettings):
    """Base of every configuration class: read-only, and ignores unrelated variables."""

    model_config = SettingsConfigDict(frozen=True, extra="ignore", env_ignore_empty=True)

    @classmethod
    def load(cls) -> Self:
        """Read the configuration, turning a validation failure into a ConfigError."""
        try:
            return cls()
        except ValidationError as error:
            names = ", ".join(
                f"{cls.model_config.get('env_prefix', '')}{'.'.join(map(str, e['loc']))}".upper()
                for e in error.errors()
            )
            raise ConfigError(f"missing or invalid setting: {names}") from error


class GithubConfig(Config):
    """GITHUB_TOKEN: the token Fugue uses for GitHub. GITHUB_API_URL: another API address.
    GITHUB_PROJECT: the number of the project Fugue uses by default."""

    model_config = SettingsConfigDict(env_prefix="GITHUB_")

    token: SecretStr
    api_url: str = "https://api.github.com"
    project: int | None = None
