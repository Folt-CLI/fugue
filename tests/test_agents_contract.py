import asyncio
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from fugue.agents import (
    AccessMode,
    AdapterConfigError,
    AdapterRegistry,
    AgentEvent,
    AgentRequest,
    AgentResult,
    Capability,
    ProviderAdapter,
    ScriptedAdapter,
    ScriptStep,
    UnknownProvider,
    UsageRecord,
    check_request,
    unsupported_result,
)
from fugue.config import RoleConfig
from fugue.models import AuthenticationMode, Role


@pytest.fixture
def agent_request(tmp_path: Path) -> AgentRequest:
    return AgentRequest(Role.IMPLEMENTER, "Implement the change", tmp_path, AccessMode.READ_ONLY)


@pytest.mark.parametrize("missing", list(Capability))
def test_check_request_capabilities(agent_request: AgentRequest, missing: Capability) -> None:
    adapter = ScriptedAdapter([], capabilities=frozenset(Capability) - {missing})
    configured = replace(
        agent_request,
        access=(
            AccessMode.WORKSPACE_WRITE
            if missing == Capability.WORKSPACE_WRITE_MODE
            else AccessMode.READ_ONLY
        ),
        session_id="session-1",
        output_schema={"type": "object"},
    )
    required = {
        Capability.READ_ONLY_MODE,
        Capability.WORKSPACE_WRITE_MODE,
        Capability.SESSION_RESUME,
        Capability.STRUCTURED_OUTPUT,
    }
    assert check_request(adapter, configured) == (missing if missing in required else None)


def test_check_request_order_and_optional_fields(agent_request: AgentRequest) -> None:
    adapter = ScriptedAdapter([], capabilities=frozenset())
    configured = replace(agent_request, session_id="", output_schema={})
    assert check_request(adapter, configured) == Capability.READ_ONLY_MODE
    adapter.capabilities = frozenset({Capability.READ_ONLY_MODE})
    assert check_request(adapter, configured) == Capability.SESSION_RESUME
    adapter.capabilities |= {Capability.SESSION_RESUME}
    assert check_request(adapter, configured) == Capability.STRUCTURED_OUTPUT
    adapter.capabilities |= {Capability.STRUCTURED_OUTPUT}
    assert check_request(adapter, configured) is None
    adapter.capabilities = frozenset({Capability.READ_ONLY_MODE})
    assert check_request(adapter, agent_request) is None


def test_unsupported_result() -> None:
    adapter = ScriptedAdapter(
        [], name="test-provider", authentication_mode=AuthenticationMode.API_KEY
    )
    result = unsupported_result(adapter, Capability.SESSION_RESUME)
    assert not result.ok
    assert result.unsupported == Capability.SESSION_RESUME
    assert result.error is not None and "session_resume" in result.error
    assert result.provider == "test-provider"
    assert result.authentication_mode == AuthenticationMode.API_KEY
    assert result.final_text == ""
    assert result.structured_output is None
    assert result.session_id is None
    assert result.usage is None
    assert result.log_path is None
    assert not result.timed_out
    assert not result.cancelled
    assert not result.provider_limited


@pytest.mark.parametrize("mode", list(AuthenticationMode))
def test_registry_build(mode: AuthenticationMode) -> None:
    config = RoleConfig.model_validate(
        {"provider": "codex", "model": "chosen-model", "authentication_mode": mode},
        context={"from_toml": True},
    )
    received: list[RoleConfig] = []
    adapter = ScriptedAdapter([], authentication_mode=mode)

    def factory(role_config: RoleConfig) -> ProviderAdapter:
        received.append(role_config)
        return adapter

    registry = AdapterRegistry()
    registry.register("codex", factory)
    assert registry.build(config) is adapter
    assert received == [config]
    assert received[0] is config


def test_registry_unknown_provider() -> None:
    with pytest.raises(UnknownProvider, match="claude"):
        AdapterRegistry().build(RoleConfig(provider="claude"))


@pytest.mark.parametrize("mode", list(AuthenticationMode))
def test_registry_rejects_authentication_mode_mismatch(mode: AuthenticationMode) -> None:
    config = RoleConfig.model_validate(
        {"provider": "codex", "authentication_mode": mode}, context={"from_toml": True}
    )
    other = (
        AuthenticationMode.API_KEY
        if mode == AuthenticationMode.SUBSCRIPTION_LOGIN
        else AuthenticationMode.SUBSCRIPTION_LOGIN
    )
    registry = AdapterRegistry()
    registry.register("codex", lambda _: ScriptedAdapter([], authentication_mode=other))
    with pytest.raises(AdapterConfigError, match="authentication mode"):
        registry.build(config)


def test_scripted_steps_events_side_effect_and_exhaustion(agent_request: AgentRequest) -> None:
    events = [AgentEvent("text", "hello"), AgentEvent("command", data={"argv": ["example"]})]
    received: list[AgentEvent] = []
    expected = AgentResult(
        ok=False,
        final_text="partial",
        structured_output={"status": "failed"},
        session_id="session-1",
        usage=UsageRecord(10, 2, 4, None, 0.01, 15),
        provider="scripted-provider",
        authentication_mode=AuthenticationMode.SUBSCRIPTION_LOGIN,
        error="scripted failure",
        timed_out=True,
        cancelled=False,
        log_path=agent_request.cwd / "log",
        unsupported=None,
    )

    def write_file(agent_request: AgentRequest) -> None:
        assert received == events
        (agent_request.cwd / "output.txt").write_text(agent_request.prompt)

    async def receive(event: AgentEvent) -> None:
        await asyncio.sleep(0)
        received.append(event)

    adapter = ScriptedAdapter(
        [ScriptStep(expected, events, write_file), ScriptStep()], name="test-provider"
    )
    assert asyncio.run(adapter.run(agent_request, receive)) is expected
    assert received == events
    assert (agent_request.cwd / "output.txt").read_text() == agent_request.prompt
    second = replace(agent_request, prompt="second")
    result = asyncio.run(adapter.run(second))
    assert result.ok and result.final_text == ""
    assert result.provider == adapter.name
    assert result.authentication_mode == adapter.authentication_mode
    assert result.error is result.unsupported is None
    with pytest.raises(AssertionError, match="exhausted"):
        asyncio.run(adapter.run(agent_request))
    assert adapter.requests == [agent_request, second, agent_request]


def test_scripted_unsupported_short_circuits(agent_request: AgentRequest) -> None:
    calls: list[AgentRequest] = []
    received: list[AgentEvent] = []

    async def receive(event: AgentEvent) -> None:
        received.append(event)

    adapter = ScriptedAdapter(
        [ScriptStep(events=[AgentEvent("text", "ready")], side_effect=calls.append, delay=0.001)],
        capabilities=frozenset({Capability.READ_ONLY_MODE}),
    )
    unsupported = replace(agent_request, output_schema={})
    result = asyncio.run(adapter.run(unsupported, receive))
    assert result.unsupported == Capability.STRUCTURED_OUTPUT
    assert calls == []
    assert received == []
    assert asyncio.run(adapter.run(agent_request, receive)).ok
    assert calls == [agent_request]
    assert received == [AgentEvent("text", "ready")]
    assert adapter.requests == [unsupported, agent_request]


def test_scripted_events_without_callback(agent_request: AgentRequest) -> None:
    adapter = ScriptedAdapter([ScriptStep(events=[AgentEvent("other")])])
    assert asyncio.run(adapter.run(agent_request)).ok


def test_scripted_delay_cancellation(agent_request: AgentRequest) -> None:
    calls: list[AgentRequest] = []
    adapter = ScriptedAdapter([ScriptStep(side_effect=calls.append, delay=60), ScriptStep()])

    async def scenario() -> None:
        task = asyncio.create_task(adapter.run(agent_request))
        await asyncio.sleep(0)
        assert adapter.requests == [agent_request]
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == []
        assert (await adapter.run(agent_request)).ok

    asyncio.run(scenario())


def test_contract_dataclasses_are_frozen(agent_request: AgentRequest) -> None:
    with pytest.raises(FrozenInstanceError):
        agent_request.prompt = "changed"  # type: ignore[misc]
    event = AgentEvent("text")
    with pytest.raises(FrozenInstanceError):
        event.text = "changed"  # type: ignore[misc]
    usage = UsageRecord(None, None, None, None, None, None)
    with pytest.raises(FrozenInstanceError):
        usage.input_tokens = 10  # type: ignore[misc]
    result = unsupported_result(ScriptedAdapter([]), Capability.SESSION_RESUME)
    with pytest.raises(FrozenInstanceError):
        result.ok = True  # type: ignore[misc]
    assert event.data == {}
    assert event.data is not AgentEvent("text").data
