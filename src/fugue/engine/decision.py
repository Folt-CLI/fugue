"""Choose the next engine action from an observed snapshot of live state."""

from dataclasses import dataclass

from fugue.github import PullRequest
from fugue.gitops import validate_staging
from fugue.models import Status


@dataclass(frozen=True)
class Snapshot:
    """Everything the engine observed about the world before deciding."""

    allowed: tuple[str, ...]
    worktree: bool
    head_sha: str | None
    ahead: tuple[str, ...]
    dirty: tuple[str, ...]
    changed: tuple[str, ...]
    remote_head_sha: str | None
    pr: PullRequest | None
    status: Status
    scope_sha256: str
    implemented_sha256: str | None


@dataclass(frozen=True)
class Prepare:
    pass


@dataclass(frozen=True)
class Implement:
    pass


@dataclass(frozen=True)
class Verify:
    """Run the gate on the dirty tree, repair on failure, commit on success."""


@dataclass(frozen=True)
class Commit:
    pass


@dataclass(frozen=True)
class Push:
    pass


@dataclass(frozen=True)
class OpenPr:
    pass


@dataclass(frozen=True)
class Done:
    pr: PullRequest
    head_sha: str | None


@dataclass(frozen=True)
class Block:
    problems: tuple[str, ...]


NextAction = Prepare | Implement | Verify | Commit | Push | OpenPr | Done | Block


def decide(snapshot: Snapshot) -> NextAction:
    """Pure decision: the first matching rule wins."""
    if snapshot.pr is not None:
        return Done(snapshot.pr, snapshot.head_sha)
    outside = validate_staging([*snapshot.dirty, *snapshot.changed], snapshot.allowed)
    if outside:
        return Block(tuple(f"Outside allowed files: {path}" for path in dict.fromkeys(outside)))
    if not snapshot.worktree or snapshot.status != Status.IN_IMPLEMENTATION:
        return Prepare()
    scope_replaced = (
        snapshot.implemented_sha256 is not None
        and snapshot.implemented_sha256 != snapshot.scope_sha256
    )
    if scope_replaced or (not snapshot.ahead and not snapshot.dirty):
        return Implement()
    if snapshot.dirty:
        return Verify()
    if snapshot.remote_head_sha != snapshot.head_sha:
        return Push()
    return OpenPr()
