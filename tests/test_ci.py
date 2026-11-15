"""Monitor reads retry empty replies without retrying other command failures."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from test_engine_ci import Harness
from test_engine_ci import harness as harness

from fugue.engine.ci import read_checks
from fugue.github import CheckResult, GitHubClient, GitHubError, PullRequest
from fugue.github.client import EmptyJSONReply
from fugue.process import CommandResult
from fugue.workflow import RunState

PR = PullRequest(27, "https://github.com/example/fugue/pull/27")
VALID = '[{"name": "unit", "bucket": "pass", "link": "check-link"}]'


@pytest.mark.parametrize("empty_count", [1, 2])
def test_monitor_recovers_from_empty_check_replies(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, empty_count: int
) -> None:
    runner = AsyncMock(side_effect=[reply("")] * empty_count + [reply(VALID)])
    client = GitHubClient("example/fugue", "example", 2, runner=runner)
    monkeypatch.setattr(harness.github, "checks", client.checks)

    outcome = harness.run()

    assert outcome.ci_green
    assert outcome.state == RunState.RUNNING
    assert runner.await_count == empty_count + 1
    assert harness.clock.sleeps == []


def reply(stdout: str, code: int = 0) -> CommandResult:
    return CommandResult(
        argv=(),
        returncode=code,
        timed_out=False,
        cancelled=False,
        stdout=stdout,
        stderr="command failed" if code else "",
        stdout_truncated=False,
        stderr_truncated=False,
        callback_line_truncated=False,
        full_output_path=None,
        duration_seconds=0,
    )


@pytest.mark.parametrize("empty_count", [0, 1, 2])
def test_monitor_read_recovers_from_empty_replies(empty_count: int) -> None:
    runner = AsyncMock(side_effect=[reply("")] * empty_count + [reply(VALID)])
    client = GitHubClient("example/fugue", "example", 2, runner=runner)
    assert asyncio.run(read_checks(lambda: client.checks(PR))) == [
        CheckResult("unit", "pass", "check-link")
    ]
    assert runner.await_count == empty_count + 1


def test_monitor_read_stops_after_three_empty_replies() -> None:
    runner = AsyncMock(side_effect=[reply("")] * 3 + [reply(VALID)])
    client = GitHubClient("example/fugue", "example", 2, runner=runner)
    with pytest.raises(EmptyJSONReply, match="Empty JSON reply") as error:
        asyncio.run(read_checks(lambda: client.checks(PR)))
    assert error.value.argv == (
        "gh",
        "pr",
        "checks",
        "27",
        "--repo",
        "example/fugue",
        "--json",
        "name,bucket,link",
    )
    assert repr(error.value.argv) in str(error.value)
    assert runner.await_count == 3


@pytest.mark.parametrize("code", [1, 2, 8])
def test_monitor_read_does_not_retry_nonzero_exit(code: int) -> None:
    runner = AsyncMock(side_effect=[reply("", code), reply(VALID)])
    client = GitHubClient("example/fugue", "example", 2, runner=runner)
    with pytest.raises(GitHubError, match="command failed") as error:
        asyncio.run(read_checks(lambda: client.checks(PR)))
    assert not isinstance(error.value, EmptyJSONReply)
    assert runner.await_count == 1


def test_monitor_read_does_not_retry_malformed_json() -> None:
    runner = AsyncMock(side_effect=[reply("invalid"), reply(VALID)])
    client = GitHubClient("example/fugue", "example", 2, runner=runner)
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(read_checks(lambda: client.checks(PR)))
    assert runner.await_count == 1
