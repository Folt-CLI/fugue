"""Load local TOML configuration without accepting unknown keys."""

import tomllib
from pathlib import Path

from pydantic import ValidationError

from fugue.config.schema import FugueConfig


class ConfigError(ValueError):
    """Configuration could not be read or validated."""


def load_config(path: Path | None) -> FugueConfig:
    if path is None:
        return FugueConfig()

    try:
        with path.open("rb") as config_file:
            data = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: {error}") from error

    try:
        return FugueConfig.model_validate(data, context={"from_toml": True})
    except ValidationError as error:
        messages = [
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_input=False)
        ]
        raise ConfigError("; ".join(messages)) from error
