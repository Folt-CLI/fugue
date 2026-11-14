"""Setup scenarios with fake registration and real loopback HTTP requests."""

from __future__ import annotations

import asyncio
import html
import json
import re
import socket
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

import pytest
from typer.testing import CliRunner

from fugue.cli import reviewer_app as command
from fugue.cli.app import app
from fugue.config import FugueConfig
from fugue.reviewer_app import flow
from fugue.reviewer_app.store import app_dir, write_private

KEY = "synthetic-private-material"


class Registration:
    def __init__(self, action: Callable[[Registration], None] | None = None) -> None:
        self.url = ""
        self.state = ""
        self.manifest: dict[str, Any] = {}
        self.page = ""
        self.calls = 0
        self.action = action

    def transport(self, code: str) -> dict[str, object]:
        self.calls += 1
        assert code == "registration-code"
        return {"id": 123, "slug": "fugue-reviewer", "pem": KEY, "client_secret": "discarded"}

    def open(self, url: str) -> bool:
        self.url = url
        assert urlsplit(url).hostname == "127.0.0.1"
        assert urlsplit(url).port
        with urlopen(url, timeout=3) as response:
            self.page = response.read().decode()
        match = re.search(r'action="([^"]+)"', self.page)
        assert match
        self.state = parse_qs(urlsplit(html.unescape(match[1])).query)["state"][0]
        match = re.search(r'name="manifest" value="([^"]+)"', self.page)
        assert match
        self.manifest = json.loads(html.unescape(match[1]))
        if self.action:
            self.action(self)
        else:
            assert self.callback() == 200
        return True

    def callback(self, **query: str) -> int:
        values = query or {"state": self.state, "code": "registration-code"}
        url = self.url.replace("/start", "/callback") + "?" + urlencode(values)
        return request_status(url)


def request_status(url: str, method: str = "GET") -> int:
    try:
        with urlopen(Request(url, method=method), timeout=3) as response:
            return int(response.status)
    except HTTPError as error:
        return error.code


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "unused-state"))
    monkeypatch.setenv("COLUMNS", "200")
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "fugue.toml").write_text('[github]\nrepo = "o/r"\n')
    return root


def directory(root: Path) -> Path:
    return app_dir(root.parent / "config", "o/r")


def invoke(root: Path, *args: str) -> Any:
    return CliRunner().invoke(
        app, ["reviewer-app", "setup", "--repo-root", str(root), "--timeout", "3", *args]
    )


def install(monkeypatch: pytest.MonkeyPatch, registration: Registration) -> None:
    monkeypatch.setattr(command, "browser_opener", registration.open)
    monkeypatch.setattr(command, "transport_hook", registration.transport)


def closed(registration: Registration) -> bool:
    parts = urlsplit(registration.url)
    assert parts.port
    with socket.socket() as connection:
        return connection.connect_ex(("127.0.0.1", parts.port)) != 0


@pytest.mark.parametrize("from_origin", [False, True])
def test_scenario_1_fresh_setup(
    root: Path, monkeypatch: pytest.MonkeyPatch, from_origin: bool
) -> None:
    if from_origin:
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(root), "remote", "add", "origin", "git@github.com:o/r.git"],
            check=True,
            capture_output=True,
        )
        (root / "fugue.toml").write_text("")
    registration = Registration()
    install(monkeypatch, registration)
    result = invoke(root)
    assert result.exit_code == 0, result.output
    assert registration.calls == 1
    key = directory(root) / "private-key.pem"
    assert key.read_text() == KEY
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory(root).stat().st_mode) == 0o700
    record = directory(root) / "app.json"
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    assert json.loads(record.read_text()) == {
        "app_id": 123,
        "slug": "fugue-reviewer",
        "repo": "o/r",
        "key_file": str(key),
    }
    assert KEY not in result.stdout and KEY not in result.stderr
    assert registration.url in result.output
    assert "pull_requests: write" in result.output and "contents: read" in result.output
    assert "https://github.com/apps/fugue-reviewer/installations/new" in result.output
    assert "reviewer_identity_separate = true" in result.output
    assert str(key) in result.output
    assert closed(registration)


def test_scenario_3_invalid_state_ignored(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def callbacks(registration: Registration) -> None:
        for query in ({"state": "wrong", "code": "x"}, {"code": "x"}):
            assert registration.callback(**query) == 400
            assert registration.calls == 0
        assert registration.callback() == 200

    registration = Registration(callbacks)
    install(monkeypatch, registration)
    assert invoke(root).exit_code == 0
    assert registration.calls == 1


@pytest.mark.parametrize("mode", ["error", "cancel", "timeout"])
def test_scenario_4_cancel_error_timeout(
    root: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    def cancel(registration: Registration) -> None:
        if mode == "error":
            assert registration.callback(state=registration.state, error="access_denied") == 400
        elif mode == "cancel":
            assert registration.callback(state=registration.state) == 400

    registration = Registration(cancel)
    install(monkeypatch, registration)
    result = invoke(root, "--timeout", "0.2")
    assert result.exit_code == 4
    assert "cancelled" in result.output or "timed out" in result.output
    assert registration.calls == 0
    assert not directory(root).exists()
    assert closed(registration)


@pytest.mark.parametrize("malformed", [False, True])
def test_scenario_5_conversion_failure(
    root: Path, monkeypatch: pytest.MonkeyPatch, malformed: bool
) -> None:
    registration = Registration()
    install(monkeypatch, registration)

    def fail(code: str) -> dict[str, object]:
        if malformed:
            return {"pem": KEY}
        raise RuntimeError(KEY)

    monkeypatch.setattr(command, "transport_hook", fail)
    result = invoke(root)
    assert result.exit_code == 4
    assert "app may already exist" in result.output
    assert "https://github.com/settings/apps" in result.output
    assert KEY not in result.stdout and KEY not in result.stderr
    assert not directory(root).exists()
    assert closed(registration)


def test_scenario_6_key_write_failure(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registration = Registration()
    install(monkeypatch, registration)

    def fail(path: Path, data: str) -> None:
        write_private(path, data)
        raise OSError(KEY)

    monkeypatch.setattr(flow, "write_private", fail)
    result = invoke(root)
    assert result.exit_code == 4
    assert registration.calls == 1
    assert "app may already exist" in result.output
    assert "https://github.com/settings/apps" in result.output
    assert KEY not in result.stdout and KEY not in result.stderr
    assert not (directory(root) / "app.json").exists()
    assert stat.S_IMODE((directory(root) / "private-key.pem").stat().st_mode) == 0o600
    assert stat.S_IMODE(directory(root).stat().st_mode) == 0o700


def test_scenario_7_existing_record(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = directory(root) / "app.json"
    write_private(path, "existing")
    registration = Registration()
    install(monkeypatch, registration)

    async def no_server(*args: object, **kwargs: object) -> None:
        pytest.fail("Server started for an existing record")

    monkeypatch.setattr(asyncio, "start_server", no_server)
    result = invoke(root)
    assert result.exit_code == 2
    assert str(path) in result.output and "Remove it to start over" in result.output
    assert registration.calls == 0 and registration.url == ""
    assert path.read_text() == "existing"


@pytest.mark.parametrize("org", [None, "team"])
def test_scenario_8_organization_target(
    root: Path, monkeypatch: pytest.MonkeyPatch, org: str | None
) -> None:
    registration = Registration()
    install(monkeypatch, registration)
    assert invoke(root, *(["--org", org] if org else [])).exit_code == 0
    target = f"/organizations/{org}/settings/apps/new" if org else "/settings/apps/new"
    assert f'action="https://github.com{target}?' in registration.page


def test_scenario_10_interrupt_and_new_state(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registrations: list[Registration] = []

    def interrupt(url: str) -> None:
        registration = Registration(lambda _: None)
        registration.open(url)
        registrations.append(registration)
        raise asyncio.CancelledError()

    monkeypatch.setattr(command, "browser_opener", interrupt)
    registration = Registration()
    monkeypatch.setattr(command, "transport_hook", registration.transport)
    result = invoke(root)
    assert result.exit_code == 6
    assert not directory(root).exists()
    assert closed(registrations[0])
    monkeypatch.setattr(command, "browser_opener", registration.open)
    assert invoke(root).exit_code == 0
    assert registration.state != registrations[0].state


def test_scenario_11_routes_and_public_record(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def routes(registration: Registration) -> None:
        for path in ("/", "/private-key.pem", "/start/", "/callback/"):
            assert request_status(registration.url.replace("/start", path)) == 404
        for method in ("POST", "PUT", "DELETE", "HEAD"):
            assert request_status(registration.url, method) == 404
            assert request_status(registration.url.replace("/start", "/callback"), method) == 404
        assert registration.calls == 0
        assert registration.callback() == 200

    registration = Registration(routes)
    install(monkeypatch, registration)
    assert invoke(root).exit_code == 0
    record = json.loads((directory(root) / "app.json").read_text())
    assert set(record) == {"app_id", "slug", "repo", "key_file"}
    assert KEY not in json.dumps(record)


def test_scenario_12_help_options() -> None:
    runner = CliRunner(env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"})
    for args, expected in (
        (["--help"], ["reviewer-app"]),
        (["reviewer-app", "--help"], ["setup"]),
        (["reviewer-app", "setup", "--help"], ["--org", "--timeout", "--config", "--repo-root"]),
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        assert all(option in result.output for option in expected)


def test_browser_unavailable_keeps_start_url(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(url: str) -> None:
        raise OSError("no browser")

    monkeypatch.setattr(command, "browser_opener", unavailable)
    result = invoke(root, "--timeout", "0.05")
    assert result.exit_code == 4
    assert "http://127.0.0.1:" in result.output
    assert "manually" in result.output


def test_injected_setup_hooks(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registration = Registration()
    config = FugueConfig.model_validate({"github": {"repo": "o/r"}})
    assert (
        command.run_setup(
            config, root, open_browser=registration.open, transport=registration.transport
        )
        == 0
    )
    output = capsys.readouterr()
    assert KEY not in output.out and KEY not in output.err
