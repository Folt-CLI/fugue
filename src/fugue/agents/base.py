"""Provider adapter contract, capabilities and normalised results."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from fugue.models import AuthenticationMode, Role


class Capability(StrEnum):
    STREAMING_EVENTS = "streaming_events"
    STRUCTURED_OUTPUT = "structured_output"
    SESSION_RESUME = "session_resume"
    CANCELLATION = "cancellation"
    READ_ONLY_MODE = "read_only_mode"
    WORKSPACE_WRITE_MODE = "workspace_write_mode"
    USAGE_TOKENS = "usage_tokens"
    USAGE_COST_ESTIMATE = "usage_cost_estimate"


class AccessMode(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"


@dataclass(frozen=True)
class AgentRequest:
    role: Role
    prompt: str
    cwd: Path
    access: AccessMode
    session_id: str | None = None
    output_schema: Mapping[str, Any] | None = None
    timeout_seconds: float | None = None
    model: str | None = None


@dataclass(frozen=True)
class UsageRecord:
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cost_estimate_usd: float | None
    duration_ms: int | None


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["text", "command", "file_change", "error", "usage", "session", "other"]
    text: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResult:
    ok: bool
    final_text: str
    structured_output: Mapping[str, Any] | None
    session_id: str | None
    usage: UsageRecord | None
    provider: str
    authentication_mode: AuthenticationMode
    error: str | None
    timed_out: bool
    cancelled: bool
    log_path: Path | None
    unsupported: Capability | None


class ProviderAdapter(Protocol):
    name: str
    authentication_mode: AuthenticationMode
    capabilities: frozenset[Capability]

    async def run(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None = None,
    ) -> AgentResult: ...


def unsupported_result(adapter: ProviderAdapter, capability: Capability) -> AgentResult:
    return AgentResult(
        ok=False,
        final_text="",
        structured_output=None,
        session_id=None,
        usage=None,
        provider=adapter.name,
        authentication_mode=adapter.authentication_mode,
        error=f"Provider {adapter.name!r} does not support capability {capability.value!r}",
        timed_out=False,
        cancelled=False,
        log_path=None,
        unsupported=capability,
    )


def check_request(adapter: ProviderAdapter, request: AgentRequest) -> Capability | None:
    required = [
        Capability.READ_ONLY_MODE
        if request.access == AccessMode.READ_ONLY
        else Capability.WORKSPACE_WRITE_MODE
    ]
    if request.session_id is not None:
        required.append(Capability.SESSION_RESUME)
    if request.output_schema is not None:
        required.append(Capability.STRUCTURED_OUTPUT)
    return next((item for item in required if item not in adapter.capabilities), None)
