"""Store artifacts in per-run directories with checksums."""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


class ArtifactError(Exception):
    """An artifact could not be stored or read."""


class ArtifactConflict(ArtifactError):
    """An artifact name already contains different content."""


@dataclass(frozen=True)
class ArtifactRef:
    """An artifact's location and content checksum."""

    name: str
    path: Path
    sha256: str
    size_bytes: int


class ArtifactStore:
    """Write and retrieve artifacts under a root directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, run_id: int, name: str) -> Path:
        if not name or name in {".", ".."} or any(c in name for c in ("/", "\\", "\0")):
            raise ArtifactError(f"Invalid artifact name: {name!r}")
        return self.root / f"run-{run_id}" / name

    @staticmethod
    def _ref(name: str, path: Path, data: bytes) -> ArtifactRef:
        return ArtifactRef(name, path, hashlib.sha256(data).hexdigest(), len(data))

    def write(self, run_id: int, name: str, data: bytes | str) -> ArtifactRef:
        """Write atomically, retaining an existing artifact with identical content."""
        path = self._path(run_id, name)
        content = data.encode("utf-8") if isinstance(data, str) else data
        temporary: Path | None = None
        try:
            try:
                existing = path.read_bytes()
            except FileNotFoundError:
                pass
            else:
                if existing != content:
                    raise ArtifactConflict(
                        f"Artifact already exists with different content: {path}"
                    )
                return self._ref(name, path, existing)

            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                existing = path.read_bytes()
                if existing != content:
                    raise ArtifactConflict(
                        f"Artifact already exists with different content: {path}"
                    ) from None
            temporary.unlink()
            temporary = None
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise ArtifactError(f"Cannot write artifact: {path}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return self._ref(name, path, content)

    def verify(self, ref: ArtifactRef) -> bool:
        """Recompute the checksum, returning false for missing or unreadable files."""
        try:
            content = ref.path.read_bytes()
        except OSError:
            return False
        return hashlib.sha256(content).hexdigest() == ref.sha256

    def read_text(self, ref: ArtifactRef) -> str:
        """Read an artifact as UTF-8 text."""
        try:
            return ref.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ArtifactError(f"Cannot read artifact as UTF-8: {ref.path}") from exc

    def open_ref(self, run_id: int, name: str) -> ArtifactRef | None:
        """Return a reference to an existing artifact, or None if absent."""
        path = self._path(run_id, name)
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ArtifactError(f"Cannot open artifact: {path}") from exc
        return self._ref(name, path, content)
