"""New ticket preconditions with fake GitHub and analyst factories."""

import asyncio
from pathlib import Path

import pytest
from test_tickets_create import FakeGitHub, draft_s
from typer.testing import CliRunner

from fugue.cli import app as cli_app
from fugue.cli import new as new_cli
from fugue.config import FugueConfig
from fugue.models import Size
from fugue.tickets import DraftStore, create_ticket

CONFIG = """[github]
repo = "o/r"
project_owner = "o"
project_number = 1
"""


@pytest.mark.parametrize("valid_root", [False, True])
def test_explicit_root_is_checked_before_external_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_root: bool
) -> None:
    """Scenarios 15, 17: configured repo cannot bypass Git validation; EOF saves."""
    config_path = tmp_path / "fugue.toml"
    config_path.write_text(CONFIG)
    root = Path(__file__).resolve().parents[1] / "src" if valid_root else tmp_path
    calls = {"github": 0, "adapter": 0}
    github = FakeGitHub()

    def build_github(config: FugueConfig, repo: str) -> FakeGitHub:
        calls["github"] += 1
        return github

    def build_adapter(config: FugueConfig) -> None:
        calls["adapter"] += 1

    def end_input(prompt: str) -> str:
        raise EOFError

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(new_cli, "github_factory", build_github)
    monkeypatch.setattr(new_cli, "adapter_factory", build_adapter)
    monkeypatch.setattr(new_cli, "reader", end_input)
    result = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb"}).invoke(
        cli_app.app,
        ["new", "--config", str(config_path), "--repo-root", str(root)],
    )
    assert github.calls == []
    if valid_root:
        assert result.exit_code == 0, result.output
        assert calls == {"github": 1, "adapter": 1}
        store = DraftStore(tmp_path / "state" / "fugue")
        assert len(store.list()) == 1
        assert store.load(store.list()[0]).repo == "o/r"
    else:
        assert result.exit_code == 2, result.output
        assert "error:" in result.output
        assert "Not in a Git repository" in result.output
        assert calls == {"github": 0, "adapter": 0}
        assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("saved_repo", ["o/r", "x/y", ""])
def test_resume_checks_repository_before_reusing_creation_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saved_repo: str
) -> None:
    """Scenarios 9, 10: a partial creation resumes only in its recorded repository."""
    config_path = tmp_path / "fugue.toml"
    config_path.write_text(CONFIG)
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    store = DraftStore(tmp_path / "state" / "fugue")
    draft = draft_s(repo=saved_repo, size=Size.M, step="review")
    github = FakeGitHub()
    github.fail["edit_issue_body"] = RuntimeError("interrupted before body edit")
    with pytest.raises(RuntimeError, match="interrupted"):
        asyncio.run(create_ticket(draft, github, store))
    assert draft.created_number == 100
    assert "body" not in draft.journal
    before = store.path(draft.id).read_bytes()
    if saved_repo != "o/r":
        github.issues[100]["body"] = "Unrelated issue in the current repository"
    original_body = github.issues[100]["body"]
    github.calls.clear()
    calls = {"github": 0, "adapter": 0}

    def build_github(config: FugueConfig, repo: str) -> FakeGitHub:
        calls["github"] += 1
        return github

    def build_adapter(config: FugueConfig) -> None:
        calls["adapter"] += 1

    answers = iter(["/create", "yes"])
    monkeypatch.setattr(new_cli, "github_factory", build_github)
    monkeypatch.setattr(new_cli, "adapter_factory", build_adapter)
    monkeypatch.setattr(new_cli, "reader", lambda prompt: next(answers))
    result = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb"}).invoke(
        cli_app.app,
        ["new", "--config", str(config_path), "--repo-root", str(root), "--resume", draft.id],
    )
    if saved_repo == "o/r":
        assert result.exit_code == 0, result.output
        assert calls == {"github": 1, "adapter": 1}
        assert github.names() == ["edit_issue_body"]
        assert "fugue run 100" in result.output
        assert "body" in store.load(draft.id).journal
    else:
        assert result.exit_code == 2, result.output
        assert draft.id in result.output
        assert "error:" in result.output and "cannot" in result.output
        assert calls == {"github": 0, "adapter": 0}
        assert github.calls == []
        assert github.issues[100]["body"] == original_body
        assert store.path(draft.id).read_bytes() == before
