"""Bound concurrent ticket runs without coupling their failures."""

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from fugue.engine.locks import TicketLock
from fugue.engine.pipeline import EngineDeps, RunOutcome
from fugue.models import Ticket


class ScheduledEngine(Protocol):
    @property
    def deps(self) -> EngineDeps: ...

    async def run_to_pr(self, ticket_number: int) -> RunOutcome: ...


async def run_many(
    tickets: Sequence[Ticket],
    engine_factory: Callable[[], ScheduledEngine],
    limit: int,
) -> list[RunOutcome | Exception | None]:
    """Return input-ordered outcomes, errors, or None for blocked tickets.

    The factory supplies an engine for each runnable ticket and owns its
    resources. Locks cover the entire run. Duplicate numbers and nonpositive
    limits are refused before starting any work. Cancellation is propagated
    after every active run has finished its cancellation cleanup.
    """
    if limit < 1:
        raise ValueError("Concurrent run limit must be positive")
    numbers = [ticket.number for ticket in tickets]
    if len(numbers) != len(set(numbers)):
        raise ValueError("The same ticket cannot be scheduled twice")
    semaphore = asyncio.Semaphore(limit)
    repo_locks: dict[Path, asyncio.Lock] = {}

    async def run(ticket: Ticket) -> RunOutcome | Exception | None:
        if ticket.blocked_by:
            return None
        async with semaphore:
            try:
                engine = engine_factory()
                engine.deps.repo_lock = repo_locks.setdefault(
                    engine.deps.repo_root.resolve(), asyncio.Lock()
                )
                with TicketLock(engine.deps.state_dir, engine.deps.github.repo, ticket.number):
                    return await engine.run_to_pr(ticket.number)
            except Exception as error:
                return error

    tasks = [asyncio.create_task(run(ticket)) for ticket in tickets]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
