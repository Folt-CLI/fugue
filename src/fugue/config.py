"""Configuration, one class per concern. Each is read from the environment and, for a setting
that belongs to a repository, from the repository's `fugue.toml`."""

import tomllib
from pathlib import Path
from typing import Any, ClassVar, Self

from pydantic import SecretStr, ValidationError
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from fugue.errors import ConfigError

FILE_NAME = "fugue.toml"


class FileSource(PydanticBaseSettingsSource):
    """Reads one section of the repository's `fugue.toml`.

    The file is looked for from the current directory upward, as far as the root of the
    repository. A secret is never read from it, whatever it holds. `keys` renames older keys.
    """

    def __init__(
        self, settings_cls: type[BaseSettings], section: str, keys: dict[str, str]
    ) -> None:
        super().__init__(settings_cls)
        self._data = self._read(section, keys)

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {
            name: value
            for name, value in self._data.items()
            if name in self.settings_cls.model_fields
            and self.settings_cls.model_fields[name].annotation is not SecretStr
        }

    @staticmethod
    def _find() -> Path | None:
        here = Path.cwd().resolve()
        for folder in (here, *here.parents):
            if (folder / FILE_NAME).is_file():
                return folder / FILE_NAME
            if (folder / ".git").exists():
                return None
        return None

    def _read(self, section: str, keys: dict[str, str]) -> dict[str, Any]:
        path = self._find()
        if path is None:
            return {}
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8")).get(section, {})
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ConfigError(f"cannot read {path}: {error}") from error
        renamed = {keys.get(name, name): value for name, value in data.items()}
        return renamed


class Config(BaseSettings):
    """Base of every configuration class: read-only, and ignores unrelated variables.

    A class that sets `section` also reads that table of `fugue.toml`, below the environment: an
    environment variable, or an argument, wins over the file, and the file over the default.
    """

    model_config = SettingsConfigDict(frozen=True, extra="ignore", env_ignore_empty=True)

    section: ClassVar[str | None] = None
    file_keys: ClassVar[dict[str, str]] = {}

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
        return (*sources, FileSource(settings_cls, cls.section, cls.file_keys))

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

    The project can also be set once for a repository, in its `fugue.toml`:

        [github]
        project = 2
    """

    model_config = SettingsConfigDict(env_prefix="GITHUB_")

    section = "github"
    file_keys = {"project_number": "project"}

    token: SecretStr
    api_url: str = "https://api.github.com"
    project: int | None = None
