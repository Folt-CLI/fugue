import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fugue.agents import (
    AccessMode,
    AdapterRegistry,
    AgentEvent,
    AgentRequest,
    Capability,
    ClaudeAdapter,
    UsageRecord,
    register_claude,
)
from fugue.config import RoleConfig
from fugue.models import AuthenticationMode, Role


@pytest.fixture
def request_data(tmp_path: Path) -> AgentRequest:
    return AgentRequest(Role.REVIEWER, "Hello\nsecond line \u2603", tmp_path, AccessMode.READ_ONLY)


@pytest.fixture
def adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ClaudeAdapter:
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(tmp_path / "record.json"))
    return ClaudeAdapter(
        executable=(sys.executable, str(Path(__file__).with_name("fake_claude.py"))),
        spool_dir=tmp_path / "logs",
    )


def record(request_data: AgentRequest) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((request_data.cwd / "record.json").read_text())
    return value


def test_read_only_events_usage_and_log(adapter: ClaudeAdapter, request_data: AgentRequest) -> None:
    events: list[AgentEvent] = []

    async def receive(event: AgentEvent) -> None:
        events.append(event)

    result = asyncio.run(adapter.run(request_data, receive))
    recorded = record(request_data)
    assert recorded["argv"] == [
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        "Read,Grep,Glob",
        "--disallowedTools",
        "Edit,Write,Bash,NotebookEdit",
    ]
    assert recorded["stdin"] == request_data.prompt
    assert recorded["cwd"] == str(request_data.cwd)
    assert [event.kind for event in events] == ["session", "text", "command", "error", "usage"]
    assert events[0].data == {"session_id": "claude-session"}
    assert events[1].text == "Hello"
    assert events[2].data["name"] == "Read"
    assert events[2].data["input"] == json.dumps({"file_path": "x" * 600})[:500]
    assert events[3].text == "read failed"
    assert result.ok and result.error is None
    assert result.final_text == "Final answer"
    assert result.session_id == "claude-session"
    assert result.usage == UsageRecord(12, 7, 4, None, 0.012, 125)
    assert result.provider == "claude"
    assert result.authentication_mode == AuthenticationMode.SUBSCRIPTION_LOGIN
    assert not result.timed_out and not result.cancelled
    assert result.unsupported is None and result.structured_output is None
    assert adapter.capabilities == frozenset(Capability)
    assert result.log_path is not None and result.log_path.parent == adapter.spool_dir
    assert '"type": "result"' in result.log_path.read_text()


@pytest.mark.parametrize("custom", [False, True])
def test_write_model_resume_schema(
    adapter: ClaudeAdapter,
    request_data: AgentRequest,
    monkeypatch: pytest.MonkeyPatch,
    custom: bool,
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "schema")
    if custom:
        adapter = ClaudeAdapter(
            executable=adapter.executable,
            spool_dir=adapter.spool_dir,
            write_tools=("Read", "Edit"),
            bash_allow=("uv run pytest", "git diff:*"),
        )
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    result = asyncio.run(
        adapter.run(
            replace(
                request_data,
                access=AccessMode.WORKSPACE_WRITE,
                model="chosen-model",
                session_id="prior-session",
                output_schema=schema,
            )
        )
    )
    assert record(request_data)["argv"] == [
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Read,Edit,Bash(uv run pytest),Bash(git diff:*)" if custom else "Read,Grep,Glob,Edit,Write",
        "--model",
        "chosen-model",
        "--resume",
        "prior-session",
        "--json-schema",
        json.dumps(schema),
    ]
    assert result.ok and result.structured_output == {"answer": "yes"}


def test_structured_output_requires_schema(
    adapter: ClaudeAdapter, request_data: AgentRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "schema")
    assert asyncio.run(adapter.run(request_data)).structured_output is None
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "ok")
    result = asyncio.run(adapter.run(replace(request_data, output_schema={})))
    assert not result.ok and result.structured_output is None
    assert result.error is not None and "missing requested structured_output" in result.error


@pytest.mark.parametrize(
    "scenario, expected",
    [
        ("error", "api_error_status=503"),
        ("rate_limited", "provider-limited: rejected"),
        ("empty", "without a result"),
        ("malformed", "Invalid Claude stream-json"),
        ("exit143", "status 143"),
    ],
)
def test_failure_paths(
    adapter: ClaudeAdapter,
    request_data: AgentRequest,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected: str,
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", scenario)
    result = asyncio.run(adapter.run(request_data))
    assert not result.ok and result.error is not None and expected in result.error
    assert result.log_path is not None and result.log_path.exists()


@pytest.mark.parametrize(
    "scenario, status, ok",
    [
        ("allowed", "allowed", True),
        ("rate_limited", "rejected", False),
    ],
)
def test_rate_limit_events(
    adapter: ClaudeAdapter,
    request_data: AgentRequest,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    status: str,
    ok: bool,
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", scenario)
    events: list[AgentEvent] = []

    async def receive(event: AgentEvent) -> None:
        events.append(event)

    result = asyncio.run(adapter.run(request_data, receive))
    assert result.ok is ok
    other = next(event for event in events if event.kind == "other")
    assert other.data["status"] == status


def test_subscription_environment(
    adapter: ClaudeAdapter, request_data: AgentRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    )
    for key in keys:
        monkeypatch.setenv(key, "must-be-removed")
    monkeypatch.setenv("FUGUE_ENV_PRESERVED", "keep")
    assert asyncio.run(adapter.run(request_data)).ok
    child_keys = record(request_data)["env_keys"]
    assert all(key not in child_keys for key in keys)
    assert "FUGUE_ENV_PRESERVED" in child_keys
    assert all(os.environ[key] == "must-be-removed" for key in keys)


def test_configuration_and_registration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="api_key"):
        ClaudeAdapter(spool_dir=tmp_path, authentication_mode=AuthenticationMode.API_KEY)
    with pytest.raises(ValueError, match="executable"):
        ClaudeAdapter(spool_dir=tmp_path, executable=())
    registry = AdapterRegistry()
    register_claude(registry)
    assert isinstance(registry.build(RoleConfig(provider="claude")), ClaudeAdapter)
    config = RoleConfig.model_validate(
        {"provider": "claude", "authentication_mode": "api_key"}, context={"from_toml": True}
    )
    with pytest.raises(ValueError, match="api_key"):
        registry.build(config)


def test_timeout(
    adapter: ClaudeAdapter, request_data: AgentRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "slow")
    result = asyncio.run(adapter.run(replace(request_data, timeout_seconds=0.5)))
    assert not result.ok and result.timed_out and not result.cancelled
    assert result.error is not None and "timed out" in result.error and "143" in result.error
    with pytest.raises(ProcessLookupError):
        os.kill(record(request_data)["pid"], 0)


def test_cancellation(
    adapter: ClaudeAdapter, request_data: AgentRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "slow")

    async def scenario() -> None:
        ready = asyncio.Event()

        async def receive(event: AgentEvent) -> None:
            if event.kind == "session":
                ready.set()

        task = asyncio.create_task(adapter.run(request_data, receive))
        try:
            await asyncio.wait_for(ready.wait(), 5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        with pytest.raises(ProcessLookupError):
            os.kill(record(request_data)["pid"], 0)
        assert list(adapter.spool_dir.glob("*.log"))

    asyncio.run(scenario())


@pytest.mark.skipif(
    os.environ.get("FUGUE_LIVE_PROVIDERS") != "1",
    reason="Live Claude requires FUGUE_LIVE_PROVIDERS=1 and an existing subscription login",
)
def test_live_claude(tmp_path: Path) -> None:
    result = asyncio.run(
        ClaudeAdapter(spool_dir=tmp_path / "logs").run(
            AgentRequest(
                Role.REVIEWER,
                "Reply with hello. Do not use tools.",
                tmp_path,
                AccessMode.READ_ONLY,
                timeout_seconds=60,
            )
        )
    )
    assert result.ok, result.error
    assert result.session_id


@pytest.mark.parametrize(
    ("status", "nested", "api_status", "expected"),
    [
        ("rejected", True, None, True),
        ("rejected", False, None, True),
        ("allowed_warning", True, None, False),
        ("allowed_warning", False, None, False),
        ("surprising_status", True, None, True),
        ("allowed", True, None, False),
        (None, False, 429, True),
        ("allowed", True, 429, True),
        (None, False, 503, False),
    ],
)
def test_explicit_provider_limit_flag(
    request_data: AgentRequest,
    status: str | None,
    nested: bool,
    api_status: int | None,
    expected: bool,
) -> None:
    stream: list[dict[str, Any]] = [
        {"type": "system", "subtype": "init", "session_id": "limited-session"},
    ]
    if status is not None:
        stream.append(
            {
                "type": "rate_limit_event",
                **({"rate_limit_info": {"status": status}} if nested else {"status": status}),
            }
        )
        # A later allowed event must not erase an earlier rejection.
        stream.append({"type": "rate_limit_event", "status": "allowed"})
    stream.append(
        {
            "type": "result",
            "result": "response",
            "is_error": api_status is not None and api_status != 429,
            "api_error_status": api_status,
        }
    )
    script = (
        "import sys; sys.stdin.read(); print("
        + repr("\n".join(json.dumps(event) for event in stream))
        + ")"
    )
    adapter = ClaudeAdapter(
        executable=(sys.executable, "-c", script),
        spool_dir=request_data.cwd / "logs",
    )
    result = asyncio.run(adapter.run(request_data))
    assert result.provider_limited is expected
    assert result.session_id == "limited-session"
    if expected:
        assert not result.ok
    if status == "rejected":
        assert result.error == "Claude provider-limited: rejected"
