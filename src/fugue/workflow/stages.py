"""Workflow stages, run states and permitted stage transitions."""

from collections.abc import Mapping
from enum import StrEnum

from fugue.config import RepoPolicy
from fugue.models import Size


class Stage(StrEnum):
    LOAD = "load"
    VALIDATE = "validate"
    ANALYSE = "analyse"
    SELECT = "select"
    PREPARE = "prepare"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    DELIVER = "deliver"
    MONITOR = "monitor"
    REVIEW = "review"
    REPAIR = "repair"
    MERGE = "merge"
    CLOSE = "close"


class RunState(StrEnum):
    RUNNING = "running"
    WAITING_FOR_SELECTION = "waiting_for_selection"
    WAITING_FOR_MERGE_APPROVAL = "waiting_for_merge_approval"
    WAITING_FOR_CI = "waiting_for_ci"
    CHECKING = "checking"
    REPAIRING = "repairing"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"
    PROVIDER_LIMITED = "provider_limited"
    COMPLETED = "completed"
    FAILED = "failed"


TRANSITIONS: Mapping[Stage, frozenset[Stage]] = {
    Stage.LOAD: frozenset({Stage.VALIDATE}),
    Stage.VALIDATE: frozenset({Stage.ANALYSE, Stage.PREPARE}),
    Stage.ANALYSE: frozenset({Stage.SELECT}),
    Stage.SELECT: frozenset({Stage.PREPARE}),
    Stage.PREPARE: frozenset({Stage.IMPLEMENT}),
    Stage.IMPLEMENT: frozenset({Stage.VERIFY}),
    Stage.VERIFY: frozenset({Stage.DELIVER, Stage.REPAIR}),
    Stage.DELIVER: frozenset({Stage.MONITOR}),
    Stage.MONITOR: frozenset({Stage.REVIEW, Stage.REPAIR, Stage.MERGE}),
    Stage.REVIEW: frozenset({Stage.REPAIR, Stage.MERGE}),
    Stage.REPAIR: frozenset({Stage.VERIFY}),
    Stage.MERGE: frozenset({Stage.CLOSE}),
    Stage.CLOSE: frozenset(),
}


def can_transition(a: Stage, b: Stage) -> bool:
    """Check the transition graph; Size-specific routing uses stages_for."""
    return b in TRANSITIONS[a]


def stages_for(size: Size, policy: RepoPolicy) -> tuple[Stage, ...]:
    """Return the happy path selected by the repository's Size rules."""
    if size == Size.XL:
        return (Stage.LOAD, Stage.VALIDATE, Stage.CLOSE)
    stages = [Stage.LOAD, Stage.VALIDATE]
    rule = policy.size_rules[size]
    if rule.analysis:
        stages.extend((Stage.ANALYSE, Stage.SELECT))
    stages.extend((Stage.PREPARE, Stage.IMPLEMENT, Stage.VERIFY, Stage.DELIVER, Stage.MONITOR))
    if rule.review:
        stages.append(Stage.REVIEW)
    stages.extend((Stage.MERGE, Stage.CLOSE))
    return tuple(stages)
