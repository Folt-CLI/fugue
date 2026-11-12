"""Scripted provider adapter for deterministic tests without provider calls."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from fugue.agents.base import (
    AgentEvent,
    AgentRequest,
    AgentResult,
    Capability,
    check_request,
    unsupported_result,
)
from fugue.models import AuthenticationMode


@dataclass(frozen=True)
class ScriptStep:
    result: AgentResult | None = None
    events: Sequence[AgentEvent] = ()
    side_effect: Callable[[AgentRequest], None] | None = None
    delay: float = 0


class ScriptedAdapter:
    def __init__(
        self,
        steps: Sequence[ScriptStep],
        *,
        name: str = "fake",
        authentication_mode: AuthenticationMode = AuthenticationMode.SUBSCRIPTION_LOGIN,
        capabilities: frozenset[Capability] = frozenset(Capability),
    ) -> None:
        self.name = name
        self.authentication_mode = authentication_mode
        self.capabilities = capabilities
        self.requests: list[AgentRequest] = []
        self._steps = tuple(steps)
        self._next_step = 0

    async def run(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None = None,
    ) -> AgentResult:
        self.requests.append(request)
        unsupported = check_request(self, request)
        if unsupported is not None:
            return unsupported_result(self, unsupported)
        if self._next_step >= len(self._steps):
            raise AssertionError("ScriptedAdapter steps exhausted")
        step = self._steps[self._next_step]
        self._next_step += 1
        if step.delay:
            await asyncio.sleep(step.delay)
        if on_event is not None:
            for event in step.events:
                await on_event(event)
        if step.side_effect is not None:
            step.side_effect(request)
        if step.result is not None:
            return step.result
        return AgentResult(
            ok=True,
            final_text="",
            structured_output=None,
            session_id=None,
            usage=None,
            provider=self.name,
            authentication_mode=self.authentication_mode,
            error=None,
            timed_out=False,
            cancelled=False,
            log_path=None,
            unsupported=None,
        )
