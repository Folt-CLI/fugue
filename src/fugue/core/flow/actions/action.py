"""The base of the reusable pieces of a flow."""

from fugue.core.github import Github
from fugue.core.reporting import Reporter


class Action:
    """One thing a flow does, with explicit inputs and a result.

    An action knows nothing about any flow: not its state, not its configuration, not what
    comes next. A flow's node adapts it: it reads what the action needs from the state, calls
    `perform`, and keeps what comes back. That way every flow shares the same actions.
    """

    def __init__(self, github: Github, reporter: Reporter) -> None:
        self.github = github
        self.reporter = reporter
