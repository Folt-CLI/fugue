"""Usage and timing reports over the run store."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fugue.cli import app as cli_app
from fugue.cli import render
from fugue.store import Store, StoreError

runner = CliRunner()


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def seed(store: Store) -> None:
    first = store.create_run(5, "o/r")
    second = store.create_run(5, "o/r")
    other = store.create_run(6, "o/r")
    store.record_usage(
        first.id,
        "implementer",
        "codex",
        "subscription_login",
        input_tokens=10,
        output_tokens=5,
        cost_estimate_usd=0.25,
        duration_ms=100,
    )
    store.record_usage(
        second.id,
        "implementer",
        "codex",
        "subscription_login",
        input_tokens=20,
        duration_ms=50,
    )
    store.record_usage(other.id, "reviewer", "claude", "api_key")


def test_usage_by_role_sums_and_keeps_unreported_null() -> None:
    with Store(":memory:") as store:
        seed(store)
        by_key = {t.key: t for t in store.usage_by("role")}
    implementer = by_key["implementer"]
    assert implementer.records == 2
    assert implementer.input_tokens == 30
    assert implementer.output_tokens == 5
    assert implementer.cost_estimate_usd == 0.25
    assert implementer.reasoning_tokens is None
    reviewer = by_key["reviewer"]
    assert reviewer.records == 1
    assert reviewer.input_tokens is None
    assert reviewer.cost_estimate_usd is None


@pytest.mark.parametrize(
    ("group_by", "keys"),
    [
        ("provider", ["claude", "codex"]),
        ("ticket", ["5", "6"]),
        ("run", ["1", "2", "3"]),
    ],
)
def test_usage_groupings(group_by: Any, keys: list[str]) -> None:
    with Store(":memory:") as store:
        seed(store)
        totals = store.usage_by(group_by)
    assert [t.key for t in totals] == keys


def test_usage_by_ticket_sums_across_runs() -> None:
    with Store(":memory:") as store:
        seed(store)
        ticket = {t.key: t for t in store.usage_by("ticket")}
    assert ticket["5"].records == 2
    assert ticket["5"].input_tokens == 30


def test_usage_ticket_filter_and_empty() -> None:
    with Store(":memory:") as store:
        seed(store)
        assert [t.key for t in store.usage_by("role", ticket_number=6)] == ["reviewer"]
        assert store.usage_by("role", ticket_number=99) == []


def test_usage_rejects_unknown_grouping() -> None:
    group_by: Any = "stage"
    with Store(":memory:") as store, pytest.raises(StoreError):
        store.usage_by(group_by)


def test_stage_durations_and_filter() -> None:
    clock = Clock()
    with Store(":memory:", clock=clock) as store:
        first = store.create_run(5, "o/r")
        second = store.create_run(6, "o/r")
        done = store.begin_attempt(first.id, "load")
        clock.now += timedelta(seconds=2.5)
        store.finish_attempt(done.id, "completed")
        store.begin_attempt(second.id, "load")
        all_rows = store.stage_durations()
        only_first = store.stage_durations(first.id)
        assert store.stage_durations(999) == []
    assert [(d.run_id, d.duration_seconds) for d in all_rows] == [(1, 2.5), (2, None)]
    assert [d.run_id for d in only_first] == [1]
    assert only_first[0].status == "completed"


def test_render_usage_labels_estimate_and_na() -> None:
    with Store(":memory:") as store:
        seed(store)
        text = render.render_usage(store.usage_by("role"), "role")
    assert text.splitlines()[0].split()[0] == "role"
    assert "0.2500" in text
    assert "n/a" in text
    assert "estimate" in text
    assert "not a bill" in text


def test_render_empty_reports() -> None:
    assert render.render_usage([], "role") == "No usage records."
    assert render.render_timing([]) == "No stage attempts."


def test_render_timing_shows_na_for_unfinished() -> None:
    clock = Clock()
    with Store(":memory:", clock=clock) as store:
        run = store.create_run(5, "o/r")
        attempt = store.begin_attempt(run.id, "load")
        clock.now += timedelta(seconds=3)
        store.finish_attempt(attempt.id, "completed")
        store.begin_attempt(run.id, "plan")
        text = render.render_timing(store.stage_durations())
    assert "3.0" in text
    assert "n/a" in text


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    directory = tmp_path / "state" / "fugue"
    directory.mkdir(parents=True)
    return directory


def test_usage_command(state_dir: Path, tmp_path: Path) -> None:
    with Store(state_dir / "fugue.db") as store:
        seed(store)
    result = runner.invoke(
        cli_app.app, ["usage", "--by", "ticket", "--ticket", "6", "--repo-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "ticket" in result.output
    assert "not a bill" in result.output
    assert "implementer" not in result.output


def test_usage_command_rejects_bad_grouping(state_dir: Path, tmp_path: Path) -> None:
    result = runner.invoke(cli_app.app, ["usage", "--by", "stage", "--repo-root", str(tmp_path)])
    assert result.exit_code == 2


def test_timing_command(state_dir: Path, tmp_path: Path) -> None:
    with Store(state_dir / "fugue.db") as store:
        run = store.create_run(5, "o/r")
        store.finish_attempt(store.begin_attempt(run.id, "load").id, "completed")
    ok = runner.invoke(cli_app.app, ["timing", "1", "--repo-root", str(tmp_path)])
    assert ok.exit_code == 0, ok.output
    assert "load" in ok.output
    missing = runner.invoke(cli_app.app, ["timing", "9", "--repo-root", str(tmp_path)])
    assert missing.exit_code == 2
