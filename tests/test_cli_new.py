"""New ticket preconditions with fake GitHub and analyst factories."""

from pathlib import Path

import pytest
from test_tickets_create import FakeGitHub
from typer.testing import CliRunner

from fugue.cli import app as cli_app
from fugue.cli import new as new_cli
from fugue.config import FugueConfig

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
        assert list((tmp_path / "state" / "fugue" / "drafts").glob("*.json"))
    else:
        assert result.exit_code == 2, result.output
        assert "error:" in result.output
        assert "Not in a Git repository" in result.output
        assert calls == {"github": 0, "adapter": 0}
        assert not (tmp_path / "state").exists()
