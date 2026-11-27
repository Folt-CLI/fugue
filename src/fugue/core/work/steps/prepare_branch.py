"""Create the branch and the worktree the work happens in."""

from pathlib import Path

from fugue.core.gitops import Worktrees
from fugue.core.reporting import WorktreeReady
from fugue.core.work.naming import Naming
from fugue.core.work.run import Run
from fugue.core.work.steps.step import Step


class PrepareBranch(Step):
    name = "Prepare the branch"

    async def run(self, run: Run) -> str:
        ticket = run.read_ticket
        naming = Naming(run.config.slug_length)
        base = run.config.base_branch or await run.github.branch.default()
        root = run.github.repository.root
        folder = run.config.worktree_root or root.parent / f"{root.name}-worktrees"
        worktrees = Worktrees(run.github.repository, run.github.config.token.get_secret_value())
        worktree = await worktrees.start(
            naming.branch(ticket), base, Path(folder) / naming.folder(ticket)
        )
        run.worktree = worktree
        await run.reporter.report(
            WorktreeReady(
                path=str(worktree.path),
                branch=worktree.branch,
                base=worktree.base,
                new_branch=worktree.new_branch,
                reused=worktree.reused,
            )
        )
        return worktree.branch
