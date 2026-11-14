"""Deterministic Codex CLI substitute; never contacts a provider."""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event), flush=True)


def main() -> None:
    argv = sys.argv[1:]
    prompt = sys.stdin.read()
    scenario = os.environ.get("FAKE_CODEX_SCENARIO", "ok")
    record = {
        "argv": argv,
        "stdin": prompt,
        "cwd": str(Path.cwd()),
        "env_keys": sorted(os.environ),
        "pid": os.getpid(),
        "schema": (
            json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text())
            if "--output-schema" in argv
            else None
        ),
    }
    with Path(os.environ["FAKE_CODEX_RECORD"]).open("a") as file:
        file.write(json.dumps(record) + "\n")
    print("non-JSON diagnostic", flush=True)
    print("null\n[]\n{}", flush=True)
    print("stderr diagnostic", file=sys.stderr, flush=True)
    emit({"type": "thread.started", "thread_id": "thread-27"})
    if scenario == "slow":
        time.sleep(60)
    message = "x" * 100000 if scenario == "large" else "fallback"
    emit({"type": "item.completed", "item": {"type": "agent_message", "text": message}})
    emit(
        {
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "pwd", "exit_code": 0},
        }
    )
    emit(
        {
            "type": "item.completed",
            "item": {"type": "file_change", "changes": [{"path": "example", "kind": "add"}]},
        }
    )
    if scenario in {"usage_limit", "usage_limit_code"}:
        error: dict[str, Any] = {
            "message": (
                "You\u2019ve hit your usage limit. Upgrade to Pro "
                "(https://chatgpt.com/explore/pro), "
                "visit https://chatgpt.com/settings/usage to purchase more credits "
                "or try again at 7:00 PM."
                if scenario == "usage_limit"
                else "provider error"
            )
        }
        if scenario == "usage_limit_code":
            error["codex_error_info"] = "usage_limit_exceeded"
        emit({"type": "error", **error})
        emit({"type": "turn.failed", "error": error})
        sys.exit(1)
    if scenario == "failure":
        emit({"type": "error", "message": "provider error"})
        emit({"type": "turn.failed", "error": {"message": "turn failed"}})
        sys.exit(1)
    emit(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 12, "cached_input_tokens": 3, "output_tokens": 7},
        }
    )
    final = {
        "schema_json": '{"answer": 42}',
        "invalid_json": "{invalid",
        "schema_array": "[]",
        "empty_final": "",
    }.get(scenario, "final")
    if scenario != "no_final":
        Path(argv[argv.index("-o") + 1]).write_text(final)


if __name__ == "__main__":
    main()
