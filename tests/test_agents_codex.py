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
    AdapterConfigError,
    AdapterRegistry,
    AgentEvent,
    AgentRequest,
    Capability,
    CodexAdapter,
    register_codex,
)
from fugue.config import RoleConfig
from fugue.models import AuthenticationMode, Role
from fugue.process import run_command


@pytest.fixture
def adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CodexAdapter:
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(tmp_path / "record.jsonl"))
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "ok")
    return CodexAdapter(
        executable=[sys.executable, str(Path(__file__).with_name("fake_codex.py"))],
        spool_dir=tmp_path / "logs",
    )


@pytest.fixture
def request_data(tmp_path: Path) -> AgentRequest:
    return AgentRequest(
        Role.IMPLEMENTER, "Prompt with unicode: café\nsecond line", tmp_path, AccessMode.READ_ONLY
    )


def record(tmp_path: Path) -> dict[str, Any]:
    records = (tmp_path / "record.jsonl").read_text().splitlines()
    parsed: dict[str, Any] = json.loads(records[-1])
    return parsed


@pytest.mark.parametrize("access", list(AccessMode))
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("options", [False, True])
def test_arguments_and_stdin(
    adapter: CodexAdapter,
    request_data: AgentRequest,
    tmp_path: Path,
    access: AccessMode,
    resume: bool,
    options: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if options:
        monkeypatch.setenv("FAKE_CODEX_SCENARIO", "schema_json")
    schema = {"type": "object", "properties": {"answer": {"type": "integer"}}}
    configured = replace(
        request_data,
        access=access,
        session_id="existing-thread" if resume else None,
        model="chosen-model" if options else None,
        output_schema=schema if options else None,
    )
    result = asyncio.run(adapter.run(configured))
    assert result.ok
    received = record(tmp_path)
    argv = received["argv"]
    final_file = argv[argv.index("-o") + 1]
    sandbox = "read-only" if access == AccessMode.READ_ONLY else "workspace-write"
    expected = ["exec"]
    if resume:
        expected += ["resume", "--json", "-o", final_file, "-c", f'sandbox_mode="{sandbox}"']
    else:
        expected += ["--json", "-o", final_file, "-s", sandbox, "-C", str(tmp_path)]
    if options:
        schema_file = argv[argv.index("--output-schema") + 1]
        expected += ["-m", "chosen-model", "--output-schema", schema_file]
        assert received["schema"] == schema
        assert result.structured_output == {"answer": 42}
    if resume:
        expected.append("existing-thread")
    expected.append("-")
    assert argv == expected
    assert received["stdin"] == request_data.prompt
    assert received["cwd"] == str(tmp_path)
    assert result.provider == "codex"
    assert result.authentication_mode == AuthenticationMode.SUBSCRIPTION_LOGIN
    assert result.session_id == "thread-27"
    assert result.error is result.unsupported is None
    assert not result.timed_out and not result.cancelled
    assert not Path(final_file).exists()


@pytest.mark.parametrize("resume", [False, True])
def test_relative_paths(
    adapter: CodexAdapter,
    request_data: AgentRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resume: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "schema_json")
    project = tmp_path / "project"
    project.mkdir()
    adapter.spool_dir = Path("logs")
    schema = {"type": "object"}
    configured = replace(
        request_data,
        cwd=Path("project"),
        session_id="existing-thread" if resume else None,
        output_schema=schema,
    )

    result = asyncio.run(adapter.run(configured))

    assert result.ok
    received = record(tmp_path)
    assert received["cwd"] == str(project)
    argv = received["argv"]
    if not resume:
        assert argv[argv.index("-C") + 1] == str(project)
    for flag in ["-o", "--output-schema"]:
        path = Path(argv[argv.index(flag) + 1])
        assert path.is_absolute()
        assert path.parent.parent == tmp_path / "logs"
    assert received["schema"] == schema
    assert result.structured_output == {"answer": 42}
    assert result.log_path is not None
    assert result.log_path.is_absolute()
    assert result.log_path.parent == tmp_path / "logs"
    assert result.log_path.exists()


def test_events_usage_and_complete_log(
    adapter: CodexAdapter,
    request_data: AgentRequest,
) -> None:
    events: list[AgentEvent] = []

    async def receive(event: AgentEvent) -> None:
        events.append(event)

    result = asyncio.run(adapter.run(request_data, receive))
    assert [event.kind for event in events] == [
        "session",
        "text",
        "command",
        "file_change",
        "usage",
    ]
    assert events[0].data == {"session_id": "thread-27"}
    assert events[1].text == "fallback"
    assert events[2].data == {"command": "pwd", "exit_code": 0}
    assert events[3].data["changes"] == [{"path": "example", "kind": "add"}]
    assert events[4].data == {"input_tokens": 12, "cached_input_tokens": 3, "output_tokens": 7}
    assert result.final_text == "final"
    assert result.usage is not None
    assert result.usage.input_tokens == 12
    assert result.usage.cached_input_tokens == 3
    assert result.usage.output_tokens == 7
    assert result.usage.reasoning_tokens is None
    assert result.usage.cost_estimate_usd is None
    assert result.usage.duration_ms is not None and result.usage.duration_ms >= 0
    assert result.log_path is not None
    assert result.log_path.parent == adapter.spool_dir
    log = result.log_path.read_text()
    assert "non-JSON diagnostic\n" in log
    assert "stderr diagnostic\n" in log
    for event_type in [
        "thread.started",
        "agent_message",
        "command_execution",
        "file_change",
        "turn.completed",
    ]:
        assert event_type in log


def test_fallback_to_agent_message(
    adapter: CodexAdapter,
    request_data: AgentRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "no_final")
    result = asyncio.run(adapter.run(request_data))
    assert result.ok and result.final_text == "fallback"


@pytest.mark.parametrize("scenario", ["invalid_json", "schema_array"])
def test_invalid_structured_output(
    adapter: CodexAdapter,
    request_data: AgentRequest,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", scenario)
    result = asyncio.run(adapter.run(replace(request_data, output_schema={})))
    assert not result.ok
    assert result.structured_output is None
    assert result.final_text == ("{invalid" if scenario == "invalid_json" else "[]")
    assert result.error is not None and "Invalid Codex structured output" in result.error


def test_empty_final_file_takes_precedence(
    adapter: CodexAdapter,
    request_data: AgentRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "empty_final")
    result = asyncio.run(adapter.run(request_data))
    assert result.ok and result.final_text == ""


def test_large_event_is_complete(
    adapter: CodexAdapter,
    request_data: AgentRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "large")
    events: list[AgentEvent] = []

    async def receive(event: AgentEvent) -> None:
        events.append(event)

    result = asyncio.run(adapter.run(request_data, receive))
    assert result.ok
    assert next(event.text for event in events if event.kind == "text") == "x" * 100000
    assert result.log_path is not None
    assert "x" * 100000 in result.log_path.read_text()


def test_failure_events(
    adapter: CodexAdapter,
    request_data: AgentRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "failure")
    events: list[AgentEvent] = []

    async def receive(event: AgentEvent) -> None:
        events.append(event)

    result = asyncio.run(adapter.run(request_data, receive))
    assert not result.ok
    assert result.error == "turn failed"
    assert [event.text for event in events if event.kind == "error"] == [
        "provider error",
        "turn failed",
    ]
    assert result.final_text == "fallback"
    assert result.usage is None


def test_timeout(
    adapter: CodexAdapter,
    request_data: AgentRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "slow")
    result = asyncio.run(adapter.run(replace(request_data, timeout_seconds=0.2)))
    assert not result.ok and result.timed_out and not result.cancelled
    assert result.error is not None and "timed out" in result.error
    assert result.log_path is not None and result.log_path.exists()


def test_cancellation_terminates_child(
    adapter: CodexAdapter,
    request_data: AgentRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "slow")

    async def scenario() -> None:
        started = asyncio.Event()

        async def receive(event: AgentEvent) -> None:
            if event.kind == "session":
                started.set()

        task = asyncio.create_task(adapter.run(request_data, receive))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        with pytest.raises(ProcessLookupError):
            os.kill(record(tmp_path)["pid"], 0)

    asyncio.run(scenario())
    assert list(adapter.spool_dir.glob("*.log"))
    assert not list(adapter.spool_dir.glob("codex-*"))


def test_api_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(AdapterConfigError, match="subscription_login"):
        CodexAdapter(spool_dir=tmp_path, authentication_mode=AuthenticationMode.API_KEY)


def test_api_key_environment_removed(
    adapter: CodexAdapter,
    request_data: AgentRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    keys = ["OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY"]
    for key in keys:
        monkeypatch.setenv(key, "must-not-reach-child")
    assert asyncio.run(adapter.run(request_data)).ok
    assert not set(keys).intersection(record(tmp_path)["env_keys"])
    for key in keys:
        assert os.environ[key] == "must-not-reach-child"


def test_unsupported_short_circuits(
    adapter: CodexAdapter,
    request_data: AgentRequest,
    tmp_path: Path,
) -> None:
    adapter.capabilities = frozenset({Capability.READ_ONLY_MODE})
    result = asyncio.run(adapter.run(replace(request_data, output_schema={})))
    assert not result.ok and result.unsupported == Capability.STRUCTURED_OUTPUT
    assert result.log_path is None
    assert not (tmp_path / "record.jsonl").exists()


def test_registration_and_capabilities() -> None:
    registry = AdapterRegistry()
    register_codex(registry)
    adapter = registry.build(RoleConfig(provider="codex"))
    assert isinstance(adapter, CodexAdapter)
    assert adapter.capabilities == frozenset(Capability) - {Capability.USAGE_COST_ESTIMATE}
    config = RoleConfig.model_validate(
        {"provider": "codex", "authentication_mode": "api_key"},
        context={"from_toml": True},
    )
    with pytest.raises(AdapterConfigError, match="subscription_login"):
        registry.build(config)


@pytest.mark.skipif(
    os.environ.get("FUGUE_LIVE_PROVIDERS") != "1",
    reason="live Codex probe; set FUGUE_LIVE_PROVIDERS=1",
)
def test_live_codex(tmp_path: Path) -> None:
    async def scenario() -> None:
        initialized = await run_command(["git", "init", str(tmp_path)])
        assert initialized.returncode == 0
        adapter = CodexAdapter(spool_dir=tmp_path / "logs")
        result = await adapter.run(
            AgentRequest(
                Role.REVIEWER, "Reply with OK.", tmp_path, AccessMode.READ_ONLY, timeout_seconds=120
            )
        )
        assert result.ok, result.error
        assert result.session_id

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Rate limit exceeded", True),
        ("You've hit your usage limit", True),
        ("USAGE_LIMIT reached", True),
        ("rate-limited", True),
        ("ratelimit reached", True),
        ("Request rejected (429)", True),
        ("Provider unavailable", False),
        ("Rate limiter configuration missing", False),
    ],
)
def test_explicit_provider_limit_flag(
    request_data: AgentRequest, message: str, expected: bool
) -> None:
    stream = [
        {"type": "thread.started", "thread_id": "limited-session"},
        {"type": "error", "message": message},
        {"type": "turn.failed", "error": {"message": "turn failed"}},
    ]
    script = (
        "import sys; sys.stdin.read(); print("
        + repr("\n".join(json.dumps(event) for event in stream))
        + ")"
    )
    adapter = CodexAdapter(
        executable=(sys.executable, "-c", script),
        spool_dir=request_data.cwd / "logs",
    )
    result = asyncio.run(adapter.run(request_data))
    assert not result.ok
    assert result.provider_limited is expected
    assert result.session_id == "limited-session"
    assert result.error == "turn failed"
