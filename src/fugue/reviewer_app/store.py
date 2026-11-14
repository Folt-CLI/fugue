"""Private, non-overwriting local reviewer app files."""

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path


def config_dir(env: Mapping[str, str], home: Path) -> Path:
    base = env.get("XDG_CONFIG_HOME")
    return Path(base) if base else home / ".config"


def app_dir(config_dir: Path, repo: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repo) or repo.split("/")[1] in {
        ".",
        "..",
    }:
        raise ValueError("Repository must be OWNER/NAME")
    return config_dir / "fugue" / "reviewer-apps" / repo.replace("/", "__")


def write_private(path: Path, data: str) -> None:
    """Publish a complete 0600 file atomically, without replacing an existing path."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    # mkstemp uses O_EXCL; linking publishes the completed file without replacement.
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".private-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


@dataclass(frozen=True)
class ReviewerAppRecord:
    app_id: int
    slug: str
    repo: str
    key_file: str

    def save(self, directory: Path) -> None:
        write_private(directory / "app.json", json.dumps(asdict(self), indent=2) + "\n")
