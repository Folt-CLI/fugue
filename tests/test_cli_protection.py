"""Read-only protection checks and CLI reporting using a fake JSON API."""

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fugue.cli import app as cli_app
from fugue.cli.protection import check_protection
from fugue.cli.render import render_protection
from fugue.github.client import GitHubError


class FakeClient:
    repo = "o/r"

    def __init__(self, responses: Mapping[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    async def _json(self, *args: str) -> Any:
        self.calls.append(args)
        assert args[0] == "api"
        assert args[2:] == ("--method", "GET")
        branch = args[1].split("/branches/", 1)[1].removesuffix("/protection")
        response = self.responses[branch]
        if isinstance(response, Exception):
            raise response
        return response


def rules(count: int = 1, stale: bool = True, resolution: bool = True) -> dict[str, object]:
    return {
        "required_pull_request_reviews": {
            "required_approving_review_count": count,
            "dismiss_stale_reviews": stale,
        },
        "required_conversation_resolution": {"enabled": resolution},
    }


def failure(message: str) -> GitHubError:
    return GitHubError(("gh", "api"), message)


def test_protected_branches_use_only_reads_and_preserve_order() -> None:
    client = FakeClient({"develop": rules(), "main": rules(2)})
    statuses = asyncio.run(check_protection(client, ["develop", "main"]))
    assert [status.branch for status in statuses] == ["develop", "main"]
    assert all(status.satisfied for status in statuses)
    assert client.calls == [
        ("api", "repos/o/r/branches/develop/protection", "--method", "GET"),
        ("api", "repos/o/r/branches/main/protection", "--method", "GET"),
    ]
    text = render_protection(statuses)
    assert text.count("satisfied") == 6
    assert "missing" not in text


@pytest.mark.parametrize(
    ("response", "missing"),
    [
        (rules(count=0), "required_approving_review_count >= 1"),
        (rules(stale=False), "dismiss_stale_reviews"),
        (rules(resolution=False), "required_conversation_resolution"),
        ({}, "required_approving_review_count >= 1"),
        ({"required_pull_request_reviews": None}, "dismiss_stale_reviews"),
    ],
)
def test_missing_rules(response: object, missing: str) -> None:
    statuses = asyncio.run(check_protection(FakeClient({"main": response}), ["main"]))
    assert not statuses[0].satisfied
    assert not statuses[0].unreadable
    assert any(
        missing in line and "missing" in line for line in render_protection(statuses).splitlines()
    )


@pytest.mark.parametrize(
    ("message", "reason", "unreadable"),
    [
        ("gh: Branch not protected (HTTP 404)", "Branch not protected", False),
        ("gh: Resource not accessible (HTTP 403)", "Permission refused (403)", True),
        ("gh: Not Found (HTTP 404)", "Protection inaccessible", True),
    ],
)
def test_unreadable_and_unprotected_reasons(message: str, reason: str, unreadable: bool) -> None:
    client = FakeClient({"main": failure(message), "develop": rules()})
    statuses = asyncio.run(check_protection(client, ["main", "develop"]))
    assert not statuses[0].satisfied
    assert statuses[0].unreadable is unreadable
    assert reason in render_protection(statuses)
    assert statuses[1].satisfied


def test_custom_branch_is_encoded() -> None:
    client = FakeClient({"release%2Fnext%23one": rules()})
    statuses = asyncio.run(check_protection(client, ["release/next#one"]))
    assert statuses[0].branch == "release/next#one"
    assert statuses[0].satisfied


@pytest.mark.parametrize("response", [[], {"required_pull_request_reviews": [1]}])
def test_malformed_response_is_an_error(response: object) -> None:
    with pytest.raises(ValueError, match="Invalid protection"):
        asyncio.run(check_protection(FakeClient({"main": response}), ["main"]))


def test_unexpected_api_error_is_preserved() -> None:
    error = failure("HTTP 500")
    with pytest.raises(GitHubError) as caught:
        asyncio.run(check_protection(FakeClient({"main": error}), ["main"]))
    assert caught.value is error


@pytest.mark.parametrize(
    ("response", "code", "expected"),
    [
        (rules(), 0, "satisfied"),
        (rules(count=0), 3, "missing"),
        (failure("Branch not protected (HTTP 404)"), 3, "Branch not protected"),
        (failure("Forbidden (HTTP 403)"), 4, "Permission refused"),
        (failure("Not Found (HTTP 404)"), 4, "Protection inaccessible"),
        (failure("Server failure (HTTP 500)"), 4, "Server failure"),
        ([], 4, "Invalid protection response"),
    ],
)
def test_cli_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: object, code: int, expected: str
) -> None:
    config = tmp_path / "custom.toml"
    config.write_text('[github]\nrepo = "o/r"\n')
    client = FakeClient({"develop": response, "main": rules()})
    monkeypatch.setattr(cli_app, "GitHubClient", lambda *args: client)
    result = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb"}).invoke(
        cli_app.app,
        ["protection", "--repo-root", str(tmp_path), "--config", str(config)],
    )
    assert result.exit_code == code, result.output
    assert expected in result.output
    assert "\x1b" not in result.output
    assert client.calls[0][1] == "repos/o/r/branches/develop/protection"


def test_cli_repeated_branches_replace_defaults_and_error_takes_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "fugue.toml").write_text('[github]\nrepo = "o/r"\n')
    client = FakeClient({"release%2Fone": rules(count=0), "stable": failure("HTTP 403")})
    monkeypatch.setattr(cli_app, "GitHubClient", lambda *args: client)
    result = CliRunner().invoke(
        cli_app.app,
        [
            "protection",
            "--repo-root",
            str(tmp_path),
            "--branch",
            "release/one",
            "--branch",
            "stable",
        ],
    )
    assert result.exit_code == 4
    assert "release/one" in result.output and "stable" in result.output
    assert len(client.calls) == 2
    assert all("develop" not in call[1] and "main" not in call[1] for call in client.calls)
