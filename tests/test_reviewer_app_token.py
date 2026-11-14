"""Reviewer app credentials stay out of command arguments and diagnostics."""

import asyncio
import base64
import json
import subprocess
import traceback
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from fugue.analysis.stage import AnalysisGitHubClient
from fugue.cli.app import _real_engine_factory
from fugue.cli.wiring import reviewer_token
from fugue.config import ConfigError, FugueConfig
from fugue.github import PullRequest
from fugue.reviewer_app.store import ReviewerAppRecord, app_dir
from fugue.reviewer_app.token import ReviewerTokenProvider, default_transport

REPO = "example/fugue"
SECRET = "private-key-text-and-token"


@pytest.fixture
def record(tmp_path: Path) -> ReviewerAppRecord:
    directory = app_dir(tmp_path, REPO)
    directory.mkdir(parents=True)
    key = directory / "private-key.pem"
    key.write_text(SECRET)
    record = ReviewerAppRecord(42, "reviewer", REPO, str(key))
    record.save(directory)
    return record


def test_jwt_and_token_flow(tmp_path: Path, record: ReviewerAppRecord) -> None:
    calls: list[tuple[str, str, Mapping[str, str]]] = []

    def transport(method: str, url: str, headers: Mapping[str, str]) -> Mapping[str, object]:
        calls.append((method, url, headers))
        return {"id": 73} if method == "GET" else {"token": SECRET}

    provider = ReviewerTokenProvider(REPO, tmp_path, transport, lambda: 1000.9)
    with patch("fugue.reviewer_app.token.subprocess.run") as signing:
        signing.return_value = subprocess.CompletedProcess([], 0, b"signature", b"")
        assert asyncio.run(provider.token()) == SECRET
    assert signing.call_args.args == (["openssl", "dgst", "-sha256", "-sign", record.key_file],)
    assert SECRET not in repr(signing.call_args.args)
    assert signing.call_args.kwargs["capture_output"] is True
    assert signing.call_args.kwargs["check"] is True
    jwt = calls[0][2]["Authorization"].removeprefix("Bearer ")
    header, payload, signature = jwt.split(".")
    assert "=" not in jwt
    assert json.loads(base64.urlsafe_b64decode(header + "==")) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    assert claims == {"iss": 42, "iat": 940, "exp": 1480}
    assert claims["exp"] - claims["iat"] <= 540
    assert base64.urlsafe_b64decode(signature + "==") == b"signature"
    assert signing.call_args.kwargs["input"] == f"{header}.{payload}".encode()
    assert jwt not in repr(signing.call_args.args)
    assert [(method, url) for method, url, _ in calls] == [
        ("GET", "https://api.github.com/repos/example/fugue/installation"),
        ("POST", "https://api.github.com/app/installations/73/access_tokens"),
    ]
    assert calls[1][2] == calls[0][2]


@pytest.mark.parametrize("failure", ["record", "key", "malformed", "wrong-repo", "bad-id"])
def test_missing_or_invalid_files(tmp_path: Path, record: ReviewerAppRecord, failure: str) -> None:
    path = app_dir(tmp_path, REPO) / "app.json"
    if failure == "record":
        path.unlink()
    elif failure == "key":
        Path(record.key_file).unlink()
    elif failure == "malformed":
        path.write_text(SECRET)
    else:
        data = json.loads(path.read_text())
        data["repo" if failure == "wrong-repo" else "app_id"] = SECRET
        path.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="fugue reviewer-app setup") as error:
        ReviewerTokenProvider(REPO, tmp_path)
    assert SECRET not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize(
    "failure", ["openssl-missing", "signing", "empty-signature", "http", "installation", "token"]
)
def test_authentication_failures_hide_secrets(
    tmp_path: Path, record: ReviewerAppRecord, failure: str
) -> None:
    calls = 0

    def transport(method: str, url: str, headers: Mapping[str, str]) -> Mapping[str, object]:
        nonlocal calls
        calls += 1
        if failure == "http":
            raise RuntimeError(f"{SECRET} {headers['Authorization']}")
        if method == "GET":
            return {"id": SECRET} if failure == "installation" else {"id": 73}
        return {"token": None}

    provider = ReviewerTokenProvider(REPO, tmp_path, transport)
    with patch("fugue.reviewer_app.token.subprocess.run") as signing:
        signing.return_value = subprocess.CompletedProcess(
            [], 0, b"" if failure == "empty-signature" else b"signature", b""
        )
        if failure == "openssl-missing":
            signing.side_effect = FileNotFoundError(SECRET)
        elif failure == "signing":
            signing.side_effect = subprocess.CalledProcessError(
                1, ["openssl"], output=SECRET, stderr=SECRET
            )
        with pytest.raises(ConfigError, match="fugue reviewer-app setup") as error:
            asyncio.run(provider.token())
    diagnostic = "".join(traceback.format_exception(error.value))
    assert SECRET not in diagnostic
    assert "Bearer " not in diagnostic
    assert calls == (
        0
        if failure in {"openssl-missing", "signing", "empty-signature"}
        else 1
        if failure in {"http", "installation"}
        else 2
    )


def test_default_transport_builds_requests_without_network() -> None:
    with patch("fugue.reviewer_app.token.urlopen") as opening:
        opening.return_value.__enter__.return_value.read.return_value = b'{"id": 73}'
        for method in ("GET", "POST"):
            assert default_transport(
                method, "https://api.github.com/test", {"Authorization": SECRET}
            ) == {"id": 73}
            request = opening.call_args.args[0]
            assert request.get_method() == method
            assert request.get_header("Authorization") == SECRET
            assert request.data == (b"" if method == "POST" else None)
            assert opening.call_args.kwargs == {"timeout": 30}
        opening.return_value.__enter__.return_value.read.return_value = b"[]"
        with pytest.raises(ValueError, match="Invalid reviewer app response"):
            default_transport("GET", "https://api.github.com/test", {})


def test_shared_wiring_honors_flag_and_config_directory(
    tmp_path: Path, record: ReviewerAppRecord
) -> None:
    config = FugueConfig.model_validate({"github": {"reviewer_identity_separate": False}})
    assert reviewer_token(config, REPO, {}, tmp_path / "unused") is None
    config.github.reviewer_identity_separate = True
    token = reviewer_token(config, REPO, {"XDG_CONFIG_HOME": str(tmp_path)}, tmp_path / "unused")
    assert token is not None
    with pytest.raises(ConfigError, match="fugue reviewer-app setup"):
        reviewer_token(config, REPO, {}, tmp_path / "unused")


@pytest.mark.parametrize("separate", [False, True])
def test_cli_engine_preserves_reviewer_token_for_review_submission(
    tmp_path: Path,
    record: ReviewerAppRecord,
    monkeypatch: pytest.MonkeyPatch,
    separate: bool,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = FugueConfig.model_validate(
        {
            "state_dir": str(tmp_path / "state"),
            "github": {
                "repo": REPO,
                "project_owner": "example",
                "project_number": 2,
                "reviewer_identity_separate": separate,
            },
        }
    )
    minted = AsyncMock(return_value=SECRET)
    submitted = AsyncMock(
        return_value=json.dumps(
            {"id": 5, "commit_id": "head", "state": "APPROVED", "body": "Clean review"}
        )
    )
    with (
        patch.object(ReviewerTokenProvider, "token", minted),
        patch.object(AnalysisGitHubClient, "_run", submitted),
    ):
        engine = _real_engine_factory(config, tmp_path, lambda message: None)
        try:
            client = engine.deps.github
            assert isinstance(client, AnalysisGitHubClient)
            event = "APPROVE" if separate else "COMMENT"
            asyncio.run(
                client.post_review(PullRequest(27, "url"), "head", "Clean review", event=event)
            )
        finally:
            engine.close()
    assert minted.await_count == int(separate)
    assert submitted.call_args.kwargs["extra_env"] == ({"GH_TOKEN": SECRET} if separate else None)
    assert SECRET not in repr(submitted.call_args.args)
