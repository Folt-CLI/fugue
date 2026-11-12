"""Drive the unmodified Codex CLI using subscription login."""

import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from fugue.agents.base import (
    AccessMode,
    AgentEvent,
    AgentRequest,
    AgentResult,
    Capability,
    UsageRecord,
    check_request,
    unsupported_result,
)
from fugue.agents.registry import AdapterConfigError, AdapterRegistry
from fugue.models import AuthenticationMode
from fugue.process import run_command


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _tokens(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _event(raw: Mapping[str, Any]) -> AgentEvent | None:
    kind = raw.get("type")
    item = _object(raw.get("item"))
    if kind == "thread.started":
        return AgentEvent("session", data={"session_id": raw.get("thread_id")})
    if kind == "item.completed" and item.get("type") == "agent_message":
        return AgentEvent("text", str(item.get("text", "")))
    if kind == "item.completed" and item.get("type") == "command_execution":
        return AgentEvent(
            "command", data={"command": item.get("command"), "exit_code": item.get("exit_code")}
        )
    if kind == "item.completed" and item.get("type") == "file_change":
        return AgentEvent("file_change", data=item)
    if kind == "turn.completed":
        return AgentEvent("usage", data=_object(raw.get("usage")))
    if kind in {"error", "turn.failed"}:
        error = _object(raw.get("error"))
        return AgentEvent("error", str(raw.get("message") or error.get("message") or kind))
    return None


class CodexAdapter:
    name = "codex"
    capabilities = frozenset(Capability) - {Capability.USAGE_COST_ESTIMATE}

    def __init__(
        self,
        *,
        executable: Sequence[str] = ("codex",),
        authentication_mode: AuthenticationMode = AuthenticationMode.SUBSCRIPTION_LOGIN,
        spool_dir: Path,
    ) -> None:
        if authentication_mode == AuthenticationMode.API_KEY:
            raise AdapterConfigError("Codex requires subscription_login authentication mode")
        if not executable:
            raise AdapterConfigError("Codex executable must not be empty")
        self.executable = tuple(executable)
        self.authentication_mode: AuthenticationMode = authentication_mode
        self.spool_dir = spool_dir

    async def run(
        self,
        request: AgentRequest,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None = None,
    ) -> AgentResult:
        unsupported = check_request(self, request)
        if unsupported is not None:
            return unsupported_result(self, unsupported)
        cwd = request.cwd.resolve()
        spool_dir = self.spool_dir.resolve()
        spool_dir.mkdir(parents=True, exist_ok=True)
        session_id = request.session_id
        last_message = ""
        error: str | None = None
        provider_limited = False
        usage_data: Mapping[str, Any] | None = None

        async def receive(line: str) -> None:
            nonlocal session_id, last_message, error, usage_data, provider_limited
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                return
            event = _event(_object(raw))
            if event is None:
                return
            if event.kind == "session" and isinstance(event.data.get("session_id"), str):
                session_id = event.data["session_id"]
            elif event.kind == "text":
                last_message = event.text
            elif event.kind == "error":
                error = event.text
                provider_limited = provider_limited or bool(
                    re.search(r"\b(?:rate|usage)[ _-]*limit(?:s|ed|ing)?\b|\b429\b", error, re.I)
                )
            elif event.kind == "usage":
                usage_data = event.data
            if on_event is not None:
                await on_event(event)

        with TemporaryDirectory(prefix="codex-", dir=spool_dir) as directory:
            final_file = Path(directory) / "final.txt"
            sandbox = "read-only" if request.access == AccessMode.READ_ONLY else "workspace-write"
            argv = [*self.executable, "exec"]
            if request.session_id is not None:
                argv += ["resume", "--json", "-o", str(final_file)]
                argv += ["-c", f'sandbox_mode="{sandbox}"']
            else:
                argv += ["--json", "-o", str(final_file), "-s", sandbox, "-C", str(cwd)]
            if request.model is not None:
                argv += ["-m", request.model]
            if request.output_schema is not None:
                schema_file = Path(directory) / "schema.json"
                schema_file.write_text(json.dumps(dict(request.output_schema)), encoding="utf-8")
                argv += ["--output-schema", str(schema_file)]
            if request.session_id is not None:
                argv.append(request.session_id)
            argv.append("-")
            # run_command overlays env on os.environ. Remove keys in the child,
            # without mutating the parent environment shared by concurrent calls.
            clean_env = ["/usr/bin/env"]
            for key in os.environ:
                if key.endswith("_API_KEY"):
                    clean_env += ["-u", key]
            result = await run_command(
                [*clean_env, *argv],
                cwd=cwd,
                stdin=request.prompt,
                timeout=request.timeout_seconds,
                max_output_bytes=0,
                max_line_bytes=None,
                spool_dir=spool_dir,
                on_stdout_line=receive,
            )
            final_text = (
                final_file.read_text(encoding="utf-8") if final_file.exists() else last_message
            )
        log_path = result.full_output_path
        if log_path is None:
            log_path = spool_dir / f"codex-{uuid4().hex}.log"
            log_path.touch()
        structured_output: Mapping[str, Any] | None = None
        if request.output_schema is not None:
            try:
                parsed = json.loads(final_text)
                if not isinstance(parsed, dict):
                    raise ValueError("structured output must be a JSON object")
                structured_output = parsed
            except (ValueError, json.JSONDecodeError) as exc:
                error = f"Invalid Codex structured output: {exc}"
        if result.timed_out:
            error = error or "Codex timed out"
        elif result.returncode != 0:
            error = error or f"Codex exited with status {result.returncode}; see {log_path}"
        usage = None
        if usage_data is not None:
            usage = UsageRecord(
                input_tokens=_tokens(usage_data.get("input_tokens")),
                cached_input_tokens=_tokens(usage_data.get("cached_input_tokens")),
                output_tokens=_tokens(usage_data.get("output_tokens")),
                reasoning_tokens=_tokens(usage_data.get("reasoning_tokens")),
                cost_estimate_usd=None,
                duration_ms=int(result.duration_seconds * 1000),
            )
        return AgentResult(
            ok=error is None and result.returncode == 0 and not result.timed_out,
            final_text=final_text,
            structured_output=structured_output,
            session_id=session_id,
            usage=usage,
            provider=self.name,
            authentication_mode=self.authentication_mode,
            error=error,
            timed_out=result.timed_out,
            cancelled=result.cancelled,
            log_path=log_path,
            unsupported=None,
            provider_limited=provider_limited
            or bool(
                re.search(r"\b(?:rate|usage)[ _-]*limit(?:s|ed|ing)?\b|\b429\b", error or "", re.I)
            ),
        )


def register_codex(registry: AdapterRegistry) -> None:
    """Register Codex without making the registry import provider adapters."""
    registry.register(
        "codex",
        lambda config: CodexAdapter(
            authentication_mode=config.authentication_mode,
            spool_dir=Path(".fugue/logs/codex"),
        ),
    )
