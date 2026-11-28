"""Looking at and changing the settings: what each one is, where its value comes from, and
writing it to a settings file."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pygit2
from pydantic import SecretStr, TypeAdapter, ValidationError
from pydantic_core import PydanticUndefined

from fugue.config import AgentsConfig, Config, GithubConfig
from fugue.configfiles import ConfigFiles, Scope
from fugue.core.work.config import WorkConfig
from fugue.errors import ConfigError

SECTIONS: dict[str, type[Config]] = {
    "github": GithubConfig,
    "work": WorkConfig,
    "agents": AgentsConfig,
}


@dataclass(frozen=True)
class Setting:
    """One setting, its value, and where the value comes from."""

    key: str
    value: Any
    source: str
    local_only: bool = False
    secret: bool = False

    @property
    def shown(self) -> str:
        """The value as text; a secret only says whether it is set."""
        if self.secret:
            return "set" if self.value else "not set"
        return "-" if self.value is None else str(self.value)


class Settings:
    """Every setting Fugue has, from the registry of configuration classes.

    settings = Settings()
    settings.get("github.project")          # value and source
    settings.set("github.project", "2", Scope.LOCAL)
    """

    def __init__(self, files: ConfigFiles | None = None) -> None:
        self.files = files or ConfigFiles()

    # ----- reading -----

    def all(self) -> list[Setting]:
        return [
            self._resolve(section, cls, name)
            for section, cls in SECTIONS.items()
            for name in cls.model_fields
        ]

    def get(self, key: str) -> Setting:
        section, cls, name = self._find(key)
        return self._resolve(section, cls, name)

    def ignored(self) -> list[str]:
        """Settings found in the global file that are not allowed there, so they have no effect."""
        found = []
        for section, cls in SECTIONS.items():
            for name in self.files.read(Scope.GLOBAL, section):
                if name in cls.local_only:
                    found.append(f"{section}.{name}")
        return found

    # ----- writing -----

    def set(self, key: str, raw: str, scope: Scope) -> Path:
        """Write a setting to a scope's file, after checking it is a valid value."""
        section, cls, name = self._find(key)
        self._check_scope(key, cls, name, scope)
        field = cls.model_fields[name]
        if field.annotation is SecretStr:
            raise ConfigError(f"{key} is a secret, so it is set with an environment variable only")
        adapter = TypeAdapter(cast(Any, field.annotation))
        try:
            value = adapter.validate_python(raw)
        except ValidationError as error:
            try:  # a table is given as JSON
                value = adapter.validate_json(raw)
            except ValidationError:
                reason = error.errors()[0]["msg"]
                raise ConfigError(f"{raw!r} is not a valid value for {key}: {reason}") from error
        stored = str(value) if isinstance(value, Path) else value
        return self.files.write(scope, section, name, stored)

    def unset(self, key: str, scope: Scope) -> Path:
        """Remove a setting from a scope's file."""
        section, cls, name = self._find(key)
        self._check_scope(key, cls, name, scope)
        return self.files.write(scope, section, name, None)

    def is_ignored(self, path: Path) -> bool:
        """Whether git ignores the file, so it is not committed by accident."""
        if self.files.root is None:
            return True
        repo = pygit2.Repository(str(self.files.root))
        return bool(repo.path_is_ignored(str(path.relative_to(self.files.root))))

    # ----- inside -----

    @staticmethod
    def _find(key: str) -> tuple[str, type[Config], str]:
        section, _, name = key.partition(".")
        cls = SECTIONS.get(section)
        if cls is None or name not in cls.model_fields:
            known = ", ".join(f"{s}.{n}" for s, c in SECTIONS.items() for n in c.model_fields)
            raise ConfigError(f"unknown setting {key!r}. Settings: {known}")
        return section, cls, name

    @staticmethod
    def _check_scope(key: str, cls: type[Config], name: str, scope: Scope) -> None:
        if scope is Scope.GLOBAL and name in cls.local_only:
            raise ConfigError(
                f"{key} belongs to one repository, so it cannot be global. "
                "Use --local (committed, shared) or --private (yours alone)."
            )

    def _resolve(self, section: str, cls: type[Config], name: str) -> Setting:
        field = cls.model_fields[name]
        key = f"{section}.{name}"
        secret = field.annotation is SecretStr
        local_only = name in cls.local_only
        variable = f"{cls.model_config.get('env_prefix', '')}{name}".upper()
        raw: Any = None
        source = "default"
        if field.default is not PydanticUndefined:
            raw = field.default
        if os.environ.get(variable):
            raw, source = os.environ[variable], f"environment {variable}"
        elif not secret:
            for scope in reversed(Scope.order()):
                if scope is Scope.GLOBAL and local_only:
                    continue
                data = self.files.read(scope, section)
                if name in data:
                    path = self.files.path(scope)
                    raw, source = data[name], f"{scope} ({self.short(path)})"
                    break
        value = raw
        if not secret and raw is not None:
            try:
                value = TypeAdapter(cast(Any, field.annotation)).validate_python(raw)
            except ValidationError:
                value = raw
        return Setting(key, value, source, local_only=local_only, secret=secret)

    def short(self, path: Path | None) -> str:
        if path is None:
            return "-"
        root = self.files.root
        if root is not None and path.is_relative_to(root):
            return str(path.relative_to(root))
        return str(path).replace(str(Path.home()), "~")
