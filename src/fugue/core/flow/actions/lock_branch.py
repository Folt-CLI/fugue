"""Take the lock on a branch."""

from fugue.core.flow.actions.action import Action
from fugue.core.gitops import BranchLock
from fugue.core.reporting import Ask, Message
from fugue.errors import FlowError, FlowStopped


class LockBranch(Action):
    async def perform(self, branch: str, *, run_id: str) -> BranchLock:
        """Take the lock on `branch` for this run. A branch worked by a live run is refused. A
        lock left by a run that is gone (it crashed or was killed) is only taken over when the
        person agrees."""
        lock = BranchLock(self.github.repository, branch)
        holder = lock.holder()
        if holder is None:
            lock.take(run_id)
            return lock
        if holder.alive:
            raise FlowError(
                f"{branch} is being worked by run {holder.run_id} (process {holder.pid}): "
                f"wait for it to finish"
            )
        answer = await self.reporter.ask(
            Ask(
                text=f"{branch} was locked by run {holder.run_id}, which is no longer running. "
                f"Take the lock over?",
                default="yes",
            )
        )
        if answer.value != "yes":
            raise FlowStopped(f"{branch} is locked by a run that is gone")
        lock.take(run_id, over=holder)
        await self.reporter.report(Message(text=f"Took over the lock on {branch}"))
        return lock
