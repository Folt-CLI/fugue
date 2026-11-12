"""Provider adapter contracts, registry and scripted fake."""

from fugue.agents.base import (
    AccessMode,
    AgentEvent,
    AgentRequest,
    AgentResult,
    Capability,
    ProviderAdapter,
    UsageRecord,
    check_request,
    unsupported_result,
)
from fugue.agents.codex import CodexAdapter, register_codex
from fugue.agents.fake import ScriptedAdapter, ScriptStep
from fugue.agents.registry import AdapterConfigError, AdapterRegistry, UnknownProvider

__all__ = [
    "AccessMode",
    "AdapterConfigError",
    "AdapterRegistry",
    "AgentEvent",
    "AgentRequest",
    "AgentResult",
    "Capability",
    "CodexAdapter",
    "ProviderAdapter",
    "ScriptStep",
    "ScriptedAdapter",
    "UnknownProvider",
    "UsageRecord",
    "check_request",
    "register_codex",
    "unsupported_result",
]
