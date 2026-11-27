"""Create the branch and the worktree a ticket is worked in."""

from pathlib import Path

from fugue.core.flow.actions.action import Action
from fugue.core.flow.naming import Naming
from fugue.core.gitops import Worktree, Worktrees
from fugue.core.reporting import WorktreeReady
from fugue.models import Ticket


class PrepareBranch(Action):
    async def perform(
        self,
        ticket: Ticket,
        *,
        base: str | None = None,
        root: Path | None = None,
        slug_length: int = 40,
    ) -> Worktree:
        """A worktree on the ticket's branch, started from the latest `origin/<base>`.

        `base` defaults to the repository's default branch. The worktree goes in `root`, by
        default `.worktrees` inside the repository, in a folder named like the branch
        (`.worktrees/feature/1-title`). A worktree that already exists for the branch is reused.
        """
        naming = Naming(slug_length)
        repository = self.github.repository
        start = base or await self.github.branch.default()
        folder = Path(root) if root else repository.root / ".worktrees"
        branch = naming.branch(ticket)
        worktrees = Worktrees(repository, self.github.config.token.get_secret_value())
        worktree = await worktrees.start(branch, start, folder / branch)
        await self.reporter.report(
            WorktreeReady(
                path=str(worktree.path),
                branch=worktree.branch,
                base=worktree.base,
                new_branch=worktree.new_branch,
                reused=worktree.reused,
            )
        )
        return worktree
