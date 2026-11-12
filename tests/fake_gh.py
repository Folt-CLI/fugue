"""Fixture-driven fake gh; unmatched invocations fail closed.

FAKE_GH_FIXTURE names a JSON object mapping JSON-encoded argv prefixes to
response objects (stdout, stderr, exit_code, echo_env). Longest prefix wins.
FAKE_GH_RECORD names a JSON-lines invocation log, including stdin.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    argv = sys.argv[1:]
    stdin = sys.stdin.read()
    with Path(os.environ["FAKE_GH_RECORD"]).open("a") as record:
        record.write(json.dumps({"argv": argv, "stdin": stdin}) + "\n")
    fixtures: dict[str, dict[str, Any]] = json.loads(
        Path(os.environ["FAKE_GH_FIXTURE"]).read_text()
    )
    matches = [(json.loads(key), value) for key, value in fixtures.items()]
    matches = [(prefix, value) for prefix, value in matches if argv[: len(prefix)] == prefix]
    if not matches:
        print(f"Unexpected fake gh invocation: {argv!r}", file=sys.stderr)
        return 99
    _, response = max(matches, key=lambda item: len(item[0]))
    if response.get("echo_env"):
        print(json.dumps({name: name in os.environ for name in ("GITHUB_TOKEN", "GH_TOKEN")}))
    else:
        sys.stdout.write(response.get("stdout", ""))
    sys.stderr.write(response.get("stderr", ""))
    return int(response.get("exit_code", 0))


if __name__ == "__main__":
    raise SystemExit(main())
