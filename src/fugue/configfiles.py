"""The files settings are read from and written to."""

import os
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any

import tomlkit

from fugue.errors import ConfigError


class Scope(StrEnum):
    """Where a setting lives. From the weakest to the strongest:

    - `global`: `~/.config/fugue/config.toml`, for every repository on this machine
    - `local`: `.fugue/config.toml` in the repository, meant to be committed and shared
    - `private`: `.fugue/config.local.toml` in the repository, yours alone and git-ignored

    Environment variables and command-line flags come after all of them.
    """

    GLOBAL = "global"
    LOCAL = "local"
    PRIVATE = "private"

    @classmethod
    def order(cls) -> list["Scope"]:
        """Weakest first: a later scope overrides the ones before it."""
        return [cls.GLOBAL, cls.LOCAL, cls.PRIVATE]


class ConfigFiles:
    """Finds, reads and writes the settings files for the repository around `start`."""

    def __init__(self, start: Path | None = None) -> None:
        self.start = (start or Path.cwd()).resolve()
        self.root = self._find_root()

    def _find_root(self) -> Path | None:
        for folder in (self.start, *self.start.parents):
            if (folder / ".git").exists():
                return folder
        return None

    def path(self, scope: Scope) -> Path | None:
        """The file of a scope, or None for a repository scope outside any repository."""
        if scope is Scope.GLOBAL:
            base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
            return base / "fugue" / "config.toml"
        if self.root is None:
            return None
        return {
            Scope.LOCAL: self.root / ".fugue" / "config.toml",
            Scope.PRIVATE: self.root / ".fugue" / "config.local.toml",
        }[scope]

    def read(self, scope: Scope, section: str) -> dict[str, Any]:
        """The settings of one section in one scope; empty when there is no such file."""
        path = self.path(scope)
        if path is None or not path.is_file():
            return {}
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8")).get(section, {})
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ConfigError(f"cannot read {path}: {error}") from error
        return dict(data) if isinstance(data, dict) else {}

    def write(self, scope: Scope, section: str, key: str, value: Any | None) -> Path:
        """Set a key in the scope's file, keeping the rest of the file as it is; `None` removes
        it. Returns the file."""
        path = self.path(scope)
        if path is None:
            raise ConfigError(f"the {scope} scope needs a repository, and this is not in one")
        try:
            document = (
                tomlkit.parse(path.read_text(encoding="utf-8"))
                if path.is_file()
                else tomlkit.document()
            )
        except (OSError, tomlkit.exceptions.TOMLKitError) as error:
            raise ConfigError(f"cannot read {path}: {error}") from error
        table = document.get(section)
        if value is None:
            if table is not None and key in table:
                del table[key]
                if not table:
                    del document[section]
        else:
            if table is None:
                document[section] = tomlkit.table()
                table = document[section]
            table[key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomlkit.dumps(document), encoding="utf-8")
        return path
