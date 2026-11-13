"""Provider adapter contracts, registry and scripted fake."""

from fugue.agents.base import (
    AccessMode,
    AgentEvent,
    AgentRequest,
    AgentResult,
    Capability,
    ProviderAdapter,
    Unavailable,
    UsageRecord,
    check_request,
    unsupported_result,
)
from fugue.agents.claude import ClaudeAdapter, register_claude
from fugue.agents.codex import CodexAdapter, register_codex
from fugue.agents.fake import ScriptedAdapter, ScriptStep
from fugue.agents.fallback import FallbackAdapter, FallbackMember, ProviderSwitch
from fugue.agents.registry import AdapterConfigError, AdapterRegistry, UnknownProvider

__all__ = [
    "AccessMode",
    "AdapterConfigError",
    "AdapterRegistry",
    "AgentEvent",
    "AgentRequest",
    "AgentResult",
    "Capability",
    "ClaudeAdapter",
    "CodexAdapter",
    "FallbackAdapter",
    "FallbackMember",
    "ProviderAdapter",
    "ProviderSwitch",
    "ScriptStep",
    "ScriptedAdapter",
    "Unavailable",
    "UnknownProvider",
    "UsageRecord",
    "check_request",
    "register_claude",
    "register_codex",
    "unsupported_result",
]
