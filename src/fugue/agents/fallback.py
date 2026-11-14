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
        model = request.models.get(member.adapter.name, member.model)
        return replace(
            request, prompt=prompt, session_id=session, model=model, models={}, sessions={}
        )

    async def run(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None = None,
    ) -> AgentResult:
        # Reason each member did not serve the call, by chain index.
        outcomes: dict[int, tuple[str, str]] = {}
        unavailable_results: list[AgentResult] = []
        capability_skip: tuple[FallbackMember, Capability] | None = None
        note: str | None = None
        announced: set[int] = set()  # members already reported as switched away from

        async def attempt(index: int, *, force: bool) -> AgentResult | None:
            """Return a result that ends the call, or None to move on."""
            nonlocal capability_skip, note
            member = self.members[index]
            name = member.adapter.name
            member_request = self._request_for(index, request, note)
            missing = check_request(member.adapter, member_request)
            if missing is not None:
                outcomes[index] = (name, f"capability:{missing.value}")
                capability_skip = capability_skip or (member, missing)
                return None
            if (
                not force
                and self._cooling(member)
                and any(
                    not self._cooling(later)
                    and check_request(later.adapter, self._request_for(later_index, request, note))
                    is None
                    for later_index, later in enumerate(self.members[index + 1 :], index + 1)
                )
            ):
                outcomes[index] = (name, "cool_down")
                return None
            # Report only switches to a member that is really called, one per member left behind.
            for earlier in sorted(outcomes):
                if earlier != index and earlier not in announced:
                    announced.add(earlier)
                    self._switch(outcomes[earlier], name, member)
            outcomes.pop(index, None)
            result = await member.adapter.run(member_request, on_event)
            if result.ok or result.timed_out or result.cancelled:
                return result
            if result.unsupported is not None:
                outcomes[index] = (name, f"capability:{result.unsupported.value}")
                capability_skip = capability_skip or (member, result.unsupported)
                return None
            if result.unavailable is None:
                return result
            self._cooldowns[name] = self._clock() + self._cooldown
            outcomes[index] = (name, result.unavailable.value)
            unavailable_results.append(result)
            note = CONTINUATION_NOTE.format(reason=f"{name}: {result.unavailable.value}")
            return None

        for index in range(len(self.members)):
            ended = await attempt(index, force=False)
            if ended is not None:
                return ended
        # Members skipped only for cool-down are tried in order once the rest are unavailable.
        for index in [i for i, (_, reason) in sorted(outcomes.items()) if reason == "cool_down"]:
            ended = await attempt(index, force=True)
            if ended is not None:
                return ended
        reasons = [outcomes[index] for index in sorted(outcomes)]
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
