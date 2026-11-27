"""Configuration, one class per concern. Each is read from the environment and, for a setting
that can be kept in a file, from the settings files (see `Scope`)."""

from typing import Any, ClassVar, Self

from pydantic import SecretStr, ValidationError
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from fugue.configfiles import ConfigFiles, Scope
from fugue.errors import ConfigError


class FileSource(PydanticBaseSettingsSource):
    """Reads one section from the settings files, the strongest scope winning.

    A secret is never read from a file, whatever it holds, and a setting that is local to a
    repository (`local_only`) is not read from the global file. `keys` renames older keys.
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        section: str,
        keys: dict[str, str],
        local_only: frozenset[str],
    ) -> None:
        super().__init__(settings_cls)
        files = ConfigFiles()
        merged: dict[str, Any] = {}
        for scope in Scope.order():
            for name, value in files.read(scope, section).items():
                name = keys.get(name, name)
                if scope is Scope.GLOBAL and name in local_only:
                    continue
                merged[name] = value
        fields = settings_cls.model_fields
        self._data = {
            name: value
            for name, value in merged.items()
            if name in fields and fields[name].annotation is not SecretStr
        }

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._data)


class Config(BaseSettings):
    """Base of every configuration class: read-only, and ignores unrelated variables.

    A class that sets `section` also reads that table of the settings files, below the
    environment: an argument or an environment variable wins over the files, and the files over
    the default. `local_only` names the settings that belong to one repository and are never
    taken from the global file.
    """

    model_config = SettingsConfigDict(frozen=True, extra="ignore", env_ignore_empty=True)

    section: ClassVar[str | None] = None
    file_keys: ClassVar[dict[str, str]] = {}
    local_only: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources = (init_settings, env_settings)
        if cls.section is None:
            return sources
        return (*sources, FileSource(settings_cls, cls.section, cls.file_keys, cls.local_only))

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
    GITHUB_PROJECT: the number of the project Fugue uses by default.

    The project can also be set once for a repository, with `fugue config set github.project 2`
    (it lives in `.fugue/config.toml`). It is never taken from the global file.
    """

    model_config = SettingsConfigDict(env_prefix="GITHUB_")

    section = "github"
    file_keys = {"project_number": "project"}
    local_only = frozenset({"project"})

    token: SecretStr
    api_url: str = "https://api.github.com"
    project: int | None = None
