"""Provider fallback: an ordered chain of adapters tried in order, whole calls only."""

from collections.abc import Awaitable, Callable, MutableMapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from fugue.agents.base import (
    AgentEvent,
    AgentRequest,
    AgentResult,
    Capability,
    ProviderAdapter,
    check_request,
    unsupported_result,
)
from fugue.models import AuthenticationMode

CONTINUATION_NOTE = (
    "Note: an earlier provider in this role could not complete this call ({reason}). "
    "Continue the work from the current state of the repository."
)
_DETAIL_LIMIT = 200


@dataclass(frozen=True)
class FallbackMember:
    adapter: ProviderAdapter
    model: str | None = None


@dataclass(frozen=True)
class ProviderSwitch:
    """A move from one member of the chain to the next, with the structured reason."""

    from_provider: str
    to_provider: str
    reason: str
    authentication_mode: AuthenticationMode

    def describe(self) -> str:
        return (
            f"provider switch: {self.from_provider} -> {self.to_provider} "
            f"({self.reason}; {self.authentication_mode.value})"
        )


class FallbackAdapter:
    """Run a request on the first available member; never split a call across members."""

    def __init__(
        self,
        members: Sequence[FallbackMember],
        *,
        cooldowns: MutableMapping[str, datetime],
        clock: Callable[[], datetime],
        cooldown_seconds: int,
        on_switch: Callable[[ProviderSwitch], None] | None = None,
    ) -> None:
        if not members:
            raise ValueError("a fallback chain needs at least one member")
        self.members = tuple(members)
        self.name = self.members[0].adapter.name
        self.authentication_mode: AuthenticationMode = self.members[0].adapter.authentication_mode
        self.capabilities: frozenset[Capability] = frozenset(
            capability for member in self.members for capability in member.adapter.capabilities
        )
        self._cooldowns = cooldowns
        self._clock = clock
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._on_switch = on_switch

    def _cooling(self, member: FallbackMember) -> bool:
        until = self._cooldowns.get(member.adapter.name)
        return until is not None and self._clock() < until

    def _request_for(self, index: int, request: AgentRequest, note: str | None) -> AgentRequest:
        member = self.members[index]
        session = request.sessions.get(member.adapter.name)
        if session is None and not request.sessions and index == 0:
            session = request.session_id
        prompt = request.prompt
        if session is None and request.fresh_prompt is not None:
            prompt = request.fresh_prompt
        if note is not None:
            prompt = f"{prompt}\n\n{note}"
        return replace(request, prompt=prompt, session_id=session, model=member.model, sessions={})

    async def run(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None = None,
    ) -> AgentResult:
        reasons: list[tuple[str, str]] = []
        unavailable_results: list[AgentResult] = []
        capability_skip: tuple[FallbackMember, Capability] | None = None
        note: str | None = None
        for index, member in enumerate(self.members):
            name = member.adapter.name
            if reasons:
                # Every earlier member was skipped or unavailable, so reasons[-1] is the
                # member immediately before this one.
                self._switch(reasons[-1], name, member)
            member_request = self._request_for(index, request, note)
            missing = check_request(member.adapter, member_request)
            if missing is not None:
                reasons.append((name, f"capability:{missing.value}"))
                capability_skip = capability_skip or (member, missing)
                continue
            if self._cooling(member) and any(
                not self._cooling(later)
                and check_request(later.adapter, self._request_for(later_index, request, note))
                is None
                for later_index, later in enumerate(self.members[index + 1 :], index + 1)
            ):
                reasons.append((name, "cool_down"))
                continue
            result = await member.adapter.run(member_request, on_event)
            if result.ok or result.timed_out or result.cancelled:
                return result
            if result.unsupported is not None:
                reasons.append((name, f"capability:{result.unsupported.value}"))
                capability_skip = capability_skip or (member, result.unsupported)
                continue
            if result.unavailable is None:
                return result
            self._cooldowns[name] = self._clock() + self._cooldown
            reasons.append((name, result.unavailable.value))
            unavailable_results.append(result)
            note = CONTINUATION_NOTE.format(reason=f"{name}: {result.unavailable.value}")
        if not unavailable_results:
            # Nothing was served and nothing was unavailable: a capability gap, not a limit.
            assert capability_skip is not None
            skipped, missing_capability = capability_skip
            failed = unsupported_result(skipped.adapter, missing_capability)
            return replace(failed, error=self._summary(reasons, {}))
        details = {
            result.provider: (result.error or "").strip()[:_DETAIL_LIMIT]
            for result in unavailable_results
        }
        return replace(
            unavailable_results[-1],
            provider_limited=True,
            error=self._summary(reasons, details),
        )

    def _switch(self, last: tuple[str, str], to_name: str, to_member: FallbackMember) -> None:
        if self._on_switch is not None:
            self._on_switch(
                ProviderSwitch(last[0], to_name, last[1], to_member.adapter.authentication_mode)
            )

    @staticmethod
    def _summary(reasons: Sequence[tuple[str, str]], details: dict[str, str]) -> str:
        parts = []
        for name, reason in reasons:
            detail = details.get(name)
            parts.append(f"{name}: {reason}" + (f" ({detail})" if detail else ""))
        return "All providers unavailable: " + "; ".join(parts)
