"""Deterministic Claude stream-json executable; never calls a provider."""

import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event), flush=True)


def main() -> None:
    scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "ok")
    Path(os.environ["FAKE_CLAUDE_RECORD"]).write_text(
        json.dumps(
            {
                "argv": sys.argv[1:],
                "stdin": sys.stdin.read(),
                "env_keys": sorted(os.environ),
                "cwd": os.getcwd(),
                "pid": os.getpid(),
            }
        )
    )
    if scenario == "empty":
        return
    if scenario == "malformed":
        print("not-json", flush=True)
        return
    emit({"type": "system", "subtype": "init", "session_id": "claude-session"})
    if scenario == "slow":
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
        time.sleep(60)
        return
    emit(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Hello"},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "x" * 600}},
                ]
            },
        }
    )
    emit(
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "content": "read complete", "is_error": False},
                    {"type": "tool_result", "content": "read failed", "is_error": True},
                ]
            },
        }
    )
    if scenario in {"rate_limited", "allowed"}:
        emit(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "rejected" if scenario == "rate_limited" else "allowed"
                },
            }
        )
    result: dict[str, Any] = {
        "type": "result",
        "is_error": scenario == "error",
        "result": "Final answer",
        "usage": {"input_tokens": 12, "cache_read_input_tokens": 7, "output_tokens": 4},
        "total_cost_usd": 0.012,
        "duration_ms": 125,
    }
    if scenario == "error":
        result["api_error_status"] = 503
    if scenario == "schema":
        result["structured_output"] = {"answer": "yes"}
    emit(result)
    if scenario == "exit143":
        sys.exit(143)


if __name__ == "__main__":
    main()
