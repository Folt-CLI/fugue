"""Manifest and private storage contracts for reviewer app setup."""

import json
import stat
from pathlib import Path

import pytest

from fugue.reviewer_app.manifest import build_manifest, start_page
from fugue.reviewer_app.store import ReviewerAppRecord, app_dir, config_dir, write_private


def test_scenario_2_config_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert config_dir({}, home) == home / ".config"
    assert config_dir({"XDG_STATE_HOME": "/state"}, home) == home / ".config"
    assert config_dir({"XDG_CONFIG_HOME": "/config"}, home) == Path("/config")
    assert app_dir(Path("/config"), "o/r") == Path("/config/fugue/reviewer-apps/o__r")


def test_scenario_9_manifest_permissions() -> None:
    manifest = build_manifest("o/r", 'Reviewer "test"', "http://127.0.0.1:123/callback")
    assert manifest == {
        "name": 'Reviewer "test"',
        "url": "https://github.com/o/r",
        "redirect_url": "http://127.0.0.1:123/callback",
        "public": False,
        "default_permissions": {"pull_requests": "write", "contents": "read"},
    }
    page = start_page(json.dumps(manifest), "https://github.com/settings/apps/new", "state")
    assert 'method="post"' in page
    assert "?state=state" in page
    assert '<button type="submit">' in page
    assert ".submit()" in page
    assert "&quot;" in page
    docs = Path("docs/user/reviewer-identity.md").read_text()
    assert "pull_requests: write" in docs and "contents: read" in docs
    assert "metadata" in docs


def test_private_files_never_overwrite(tmp_path: Path) -> None:
    directory = app_dir(tmp_path, "o/r")
    path = directory / "private-key.pem"
    write_private(path, "original")
    with pytest.raises(FileExistsError):
        write_private(path, "replacement")
    assert path.read_text() == "original"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    ReviewerAppRecord(1, "reviewer", "o/r", str(path)).save(directory)
    with pytest.raises(FileExistsError):
        ReviewerAppRecord(2, "other", "o/r", str(path)).save(directory)
    assert json.loads((directory / "app.json").read_text())["app_id"] == 1
    assert not list(directory.glob(".private-*"))


@pytest.mark.parametrize("repo", ["../r", "o/..", "o/r/other", "/r"])
def test_repository_cannot_escape_config_directory(tmp_path: Path, repo: str) -> None:
    with pytest.raises(ValueError):
        app_dir(tmp_path, repo)


def test_conversion_request_and_secret_response(monkeypatch: pytest.MonkeyPatch) -> None:
    from io import BytesIO
    from urllib.request import Request

    from fugue.reviewer_app import flow

    calls = 0

    def response(request: Request, *, timeout: int) -> BytesIO:
        nonlocal calls
        calls += 1
        assert request.full_url == "https://api.github.com/app-manifests/a%2Fb/conversions"
        assert request.get_method() == "POST"
        assert request.data == b""
        assert not request.has_header("Authorization")
        assert timeout == 30
        return BytesIO(b'{"id": 1, "slug": "reviewer", "pem": "synthetic-private-material"}')

    monkeypatch.setattr(flow, "urlopen", response)
    app_id, slug, key = flow.convert("a/b")
    assert app_id == 1 and slug == "reviewer" and bool(key)
    assert calls == 1


def test_conversion_error_never_exposes_response() -> None:
    from fugue.reviewer_app.flow import convert

    secret = "synthetic-private-material"

    def failure(code: str) -> dict[str, object]:
        raise ValueError(secret)

    with pytest.raises(RuntimeError) as caught:
        convert("code", failure)
    assert secret not in str(caught.value)
    assert caught.value.__suppress_context__
    assert "https://github.com/settings/apps" in str(caught.value)
