"""Claude CLI provider adapter using subscription login."""

import json
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fugue.agents.base import (
    AccessMode,
    AgentEvent,
    AgentRequest,
    AgentResult,
    Capability,
    UsageRecord,
)
from fugue.agents.registry import AdapterRegistry
from fugue.models import AuthenticationMode
from fugue.process import run_command

# run_command overlays the inherited environment. Unset these in the child only,
# so concurrent calls cannot change each other's authentication mode.
_SUBSCRIPTION_UNSET = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (float, int)) and not isinstance(value, bool) else None


@dataclass
class _Stream:
    session_id: str | None
    result: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)

    def parse(self, line: str) -> list[AgentEvent]:
        try:
            item = _object(json.loads(line))
        except json.JSONDecodeError:
            self.errors.append("Invalid Claude stream-json event")
            return [AgentEvent("error", self.errors[-1])]
        kind = item.get("type")
        if kind == "system" and item.get("subtype") == "init":
            session_id = item.get("session_id")
            if isinstance(session_id, str):
                self.session_id = session_id
            return [AgentEvent("session", data={"session_id": self.session_id})]
        if kind == "assistant":
            events: list[AgentEvent] = []
            content = _object(item.get("message")).get("content", [])
            if not isinstance(content, list):
                return events
            for raw_block in content:
                block = _object(raw_block)
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    events.append(AgentEvent("text", block["text"]))
                elif block.get("type") == "tool_use":
                    summary = json.dumps(block.get("input", {}), ensure_ascii=False)[:500]
                    events.append(
                        AgentEvent("command", data={"name": block.get("name"), "input": summary})
                    )
            return events
        if kind == "user":
            content = _object(item.get("message")).get("content", [])
            if not isinstance(content, list):
                return []
            return [
                AgentEvent("error", str(block.get("content", "")), block)
                for raw_block in content
                if (block := _object(raw_block)).get("type") == "tool_result"
                and block.get("is_error") is True
            ]
        if kind == "rate_limit_event":
            info = _object(item.get("rate_limit_info"))
            status = info.get("status", item.get("status"))
            if status != "allowed":
                self.errors.append(f"Claude provider-limited: {status}")
            return [AgentEvent("other", data={**item, "status": status})]
        if kind == "result":
            self.result = item
            return [AgentEvent("usage", data=item)]
        return [AgentEvent("other", data=item)]

    def usage(self) -> UsageRecord | None:
        if self.result is None:
            return None
        usage = _object(self.result.get("usage"))
        return UsageRecord(
            input_tokens=_integer(usage.get("input_tokens")),
            cached_input_tokens=_integer(usage.get("cache_read_input_tokens")),
            output_tokens=_integer(usage.get("output_tokens")),
            reasoning_tokens=None,
            cost_estimate_usd=_number(self.result.get("total_cost_usd")),
            duration_ms=_integer(self.result.get("duration_ms")),
        )


class ClaudeAdapter:
    """Drive the unmodified CLI. Cancellation propagates after process cleanup.

    SIGTERM may be reported by the CLI as exit 143 (or by the process runner as
    -15); neither is a successful result, including after a timeout.
    """

    name = "claude"
    capabilities = frozenset(Capability)

    def __init__(
        self,
        *,
        executable: Sequence[str] = ("claude",),
        authentication_mode: AuthenticationMode = AuthenticationMode.SUBSCRIPTION_LOGIN,
        spool_dir: Path,
        write_tools: Sequence[str] = ("Read", "Grep", "Glob", "Edit", "Write"),
        bash_allow: Sequence[str] = (),
    ) -> None:
        if authentication_mode != AuthenticationMode.SUBSCRIPTION_LOGIN:
            raise ValueError("Claude adapter does not support api_key authentication mode")
        if not executable:
            raise ValueError("executable must not be empty")
        self.authentication_mode: AuthenticationMode = authentication_mode
        self.executable = tuple(executable)
        self.spool_dir = spool_dir
        self.write_tools = tuple(write_tools)
        self.bash_allow = tuple(bash_allow)

    def _argv(self, request: AgentRequest) -> list[str]:
        args = [*self.executable, "-p", "--output-format", "stream-json", "--verbose"]
        if request.access == AccessMode.READ_ONLY:
            args += [
                "--permission-mode",
                "dontAsk",
                "--allowedTools",
                "Read,Grep,Glob",
                "--disallowedTools",
                "Edit,Write,Bash,NotebookEdit",
            ]
        else:
            args += [
                "--permission-mode",
                "acceptEdits",
                "--allowedTools",
                ",".join([*self.write_tools, *(f"Bash({b})" for b in self.bash_allow)]),
            ]
        if request.model is not None:
            args += ["--model", request.model]
        if request.session_id is not None:
            args += ["--resume", request.session_id]
        if request.output_schema is not None:
            args += ["--json-schema", json.dumps(dict(request.output_schema))]
        return args

    async def run(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None = None,
    ) -> AgentResult:
        stream = _Stream(request.session_id)

        async def receive(line: str) -> None:
            for event in stream.parse(line):
                if on_event is not None:
                    await on_event(event)

        args = ["/usr/bin/env", *(arg for key in _SUBSCRIPTION_UNSET for arg in ("-u", key))]
        command = await run_command(
            [*args, *self._argv(request)],
            cwd=request.cwd,
            stdin=request.prompt,
            timeout=request.timeout_seconds,
            spool_dir=self.spool_dir,
            max_output_bytes=0,
            max_line_bytes=None,
            on_stdout_line=receive,
        )
        log_path = command.full_output_path
        if log_path is None:
            self.spool_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=self.spool_dir, suffix=".log", delete=False
            ) as log:
                log_path = Path(log.name)
        result = stream.result or {}
        errors = stream.errors
        if command.timed_out:
            errors.append("Claude command timed out")
        if command.returncode != 0:
            errors.append(f"Claude command exited with status {command.returncode}")
        if stream.result is None:
            errors.append("Claude stream ended without a result")
        if result.get("is_error") is True:
            errors.append(
                f"Claude result error: {result.get('result', result.get('errors', ''))}; "
                f"api_error_status={result.get('api_error_status')}"
            )
        structured = result.get("structured_output") if request.output_schema is not None else None
        if request.output_schema is not None and not isinstance(structured, dict):
            errors.append("Claude result is missing requested structured_output")
        final_text = result.get("result", "")
        return AgentResult(
            ok=not errors,
            final_text=final_text if isinstance(final_text, str) else "",
            structured_output=structured if isinstance(structured, dict) else None,
            session_id=stream.session_id,
            usage=stream.usage(),
            provider=self.name,
            authentication_mode=self.authentication_mode,
            error="; ".join(errors) if errors else None,
            timed_out=command.timed_out,
            cancelled=command.cancelled,
            log_path=log_path,
            unsupported=None,
        )


def register_claude(registry: AdapterRegistry) -> None:
    """Register Claude with logs in the local Fugue state directory."""
    registry.register(
        "claude",
        lambda config: ClaudeAdapter(
            authentication_mode=config.authentication_mode,
            spool_dir=Path(".fugue") / "logs" / "claude",
        ),
    )
