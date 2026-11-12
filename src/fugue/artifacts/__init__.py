"""Files produced or consumed by a run, stored with SHA-256 checksums."""

from fugue.artifacts.store import ArtifactConflict, ArtifactError, ArtifactRef, ArtifactStore

__all__ = ["ArtifactConflict", "ArtifactError", "ArtifactRef", "ArtifactStore"]
