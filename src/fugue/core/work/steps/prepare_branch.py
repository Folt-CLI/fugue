"""Create the branch and the worktree the work happens in."""

from dataclasses import dataclass
from pathlib import Path

from pydantic_graph import GraphRunContext

from fugue.core.gitops import Worktrees
from fugue.core.reporting import WorktreeReady
from fugue.core.work.naming import Naming
from fugue.core.work.state import WorkDeps, WorkState
from fugue.core.work.steps.finish import Finish
from fugue.core.work.steps.step import Step


@dataclass
class PrepareBranch(Step):
    name = "Prepare the branch"

    async def act(self, state: WorkState, deps: WorkDeps) -> str:
        ticket = state.read_ticket
        naming = Naming(deps.config.slug_length)
        base = deps.config.base_branch or await deps.github.branch.default()
        root = deps.github.repository.root
        folder = deps.config.worktree_root or root.parent / f"{root.name}-worktrees"
        worktrees = Worktrees(deps.github.repository, deps.github.config.token.get_secret_value())
        worktree = await worktrees.start(
            naming.branch(ticket), base, Path(folder) / naming.folder(ticket)
        )
        state.worktree = worktree
        await deps.reporter.report(
            WorktreeReady(
                path=str(worktree.path),
                branch=worktree.branch,
                base=worktree.base,
                new_branch=worktree.new_branch,
                reused=worktree.reused,
            )
        )
        return worktree.branch

    async def run(self, ctx: GraphRunContext[WorkState, WorkDeps]) -> Finish:
        await self.execute(ctx)
        return Finish()
