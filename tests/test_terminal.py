"""Terminal layer: degradation, wrapping, progress and error output."""

import re
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from fugue import __version__
from fugue.cli import app as cli_app
from fugue.cli import render, terminal

ESCAPE = "\x1b"
runner = CliRunner()


COLOUR_CODE = re.compile(r"\x1b\[[0-9;]*(?:3[0-79]|9[0-7]|38;)[0-9;]*m")


def terminal_console(env: dict[str, str]) -> tuple[Console, StringIO]:
    sink = StringIO()
    return terminal.console_for({**env, "FORCE_COLOR": "1"}, file=sink), sink


def test_version_output_has_no_escapes() -> None:
    result = runner.invoke(cli_app.app, ["--version"])
    assert result.exit_code == 0
    assert result.output == f"fugue {__version__}\n"
    assert ESCAPE not in result.output


def test_piped_output_has_no_escapes_or_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "xterm-256color")
    sink = StringIO()
    console = terminal.console_for(file=sink)
    terminal.print_text("error: boom", console=console)
    terminal.print_table(["run", "state"], [["1", "ok"]], console=console)
    terminal.print_panel("Title", "body", console=console)
    terminal.print_markdown("# Heading", console=console)
    assert ESCAPE not in sink.getvalue()
    assert "\r" not in sink.getvalue()


def test_progress_is_one_plain_line_per_stage_when_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "xterm")
    with terminal.progress_callback() as progress:
        assert not progress.live
        progress("load: started")
        progress("load: completed")
    assert capsys.readouterr().out == "load: started\nload: completed\n"


@pytest.mark.parametrize("env", [{"NO_COLOR": "1", "TERM": "xterm"}, {"TERM": "dumb"}])
def test_no_colour_keeps_meaning_in_text(env: dict[str, str]) -> None:
    console, sink = terminal_console(env)
    terminal.print_text("error: failed", console=console)
    terminal.print_text("warning: metered", console=console)
    terminal.print_text("next: fugue status", console=console)
    out = sink.getvalue()
    stripped = re.sub(r"\x1b\[[0-9;]*m", "", out)
    assert stripped == "error: failed\nwarning: metered\nnext: fugue status\n"
    if env.get("TERM") == "dumb":
        assert ESCAPE not in out
    else:
        assert COLOUR_CODE.search(out) is None


def test_colour_is_used_on_a_colour_terminal() -> None:
    console, sink = terminal_console({"TERM": "xterm-256color", "COLORTERM": "truecolor"})
    terminal.print_text("error: failed", console=console)
    assert ESCAPE in sink.getvalue()


def test_blocking_wording_survives_without_colour() -> None:
    console, sink = terminal_console({"NO_COLOR": "1", "TERM": "xterm"})
    terminal.print_text("Blocking: missing test", console=console)
    terminal.print_text("Non-blocking: naming", console=console)
    assert sink.getvalue() == "Blocking: missing test\nNon-blocking: naming\n"


def test_narrow_table_wraps_without_truncating_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "40")
    rows = [
        ["1234567", "owner/some-long-repository-name", "12345", "waiting_for_ci", "2026-01-01"],
    ]
    lines = terminal.table_text(["id", "repo", "ticket", "state", "updated"], rows)
    text = "\n".join(lines)
    assert "1234567" in text and "12345" in text
    assert all(len(line) <= 40 for line in lines)
    assert "…" not in text


def test_render_runs_at_narrow_width_keeps_run_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "40")
    lines = render._table(["run", "ticket", "result"], [["987654", "4321", "x" * 80]])
    assert lines[1].split()[:2] == ["987654", "4321"]


def test_table_cells_with_brackets_are_literal() -> None:
    assert terminal.table_text(["role", "model"], [["[roles.x]", "[#1]"]])[1].split() == [
        "[roles.x]",
        "[#1]",
    ]


def test_error_goes_to_stderr_with_next_step(capsys: pytest.CaptureFixture[str]) -> None:
    terminal.error("Unknown run id 9", "fugue status")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: Unknown run id 9\nnext: fugue status\n"


def test_unknown_run_id_prints_next_step_and_keeps_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    result = runner.invoke(cli_app.app, ["status", "9", "--repo-root", str(tmp_path)])
    assert result.exit_code == cli_app.EXIT_USAGE
    assert "error: Unknown run id 9\nnext: fugue status\n" in result.output


class FakeStatus:
    def __init__(self) -> None:
        self.events: list[str] = []

    def update(self, text: object) -> None:
        self.events.append(f"update:{text}")

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")


def live_progress(
    monkeypatch: pytest.MonkeyPatch, *, batch: bool = False
) -> tuple[terminal.Progress, FakeStatus]:
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("FORCE_COLOR", "1")
    progress = terminal.Progress(batch=batch)
    fake = FakeStatus()
    monkeypatch.setattr(progress._console, "status", lambda *a, **k: fake)
    return progress, fake


def test_single_run_uses_one_live_spinner_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    progress, fake = live_progress(monkeypatch)
    assert progress.live
    with progress:
        progress("implement: started")
        progress("implement: completed")
    assert fake.events == [
        "start",
        "update:implement: started",
        "update:implement: completed",
        "stop",
    ]


def test_spinner_stops_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    progress, fake = live_progress(monkeypatch)
    with pytest.raises(KeyboardInterrupt), progress:
        raise KeyboardInterrupt
    assert fake.events[-1] == "stop"
    assert terminal._active is None


def test_batch_never_uses_live(monkeypatch: pytest.MonkeyPatch) -> None:
    progress, fake = live_progress(monkeypatch, batch=True)
    assert not progress.live
    with progress:
        progress("[#1] load: started")
    assert fake.events == []


def test_interrupted_single_run_keeps_exit_code_6(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from test_cli import install_engine, run_command

    install_engine(monkeypatch, KeyboardInterrupt())
    result = run_command(tmp_path)
    assert result.exit_code == 6
    assert "error: run interrupted" in result.output


def test_batch_lines_stay_whole_and_prefixed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from test_cli import batch_command, install_engine, waiting

    install_engine(monkeypatch, {n: waiting(n) for n in (1, 2)})
    result = batch_command(tmp_path, "1", "2")
    progress_lines = [line for line in result.output.splitlines() if line.startswith("[#")]
    assert progress_lines
    assert all(line[2:3] in "12" and line[3:5] == "] " for line in progress_lines)
    assert ESCAPE not in result.output


def test_config_show_prints_brackets_literally(tmp_path: Path) -> None:
    (tmp_path / "fugue.toml").write_text("")
    result = runner.invoke(
        cli_app.app, ["config", "show", "--config", str(tmp_path / "fugue.toml")]
    )
    assert result.exit_code == 0
    expected = render.render_config(cli_app._load(tmp_path / "fugue.toml", tmp_path))
    assert result.output == expected + "\n"
    assert "[roles." in result.output


def test_use_console_routes_prints(capsys: pytest.CaptureFixture[str]) -> None:
    sink = StringIO()
    console = terminal.console_for(file=sink)
    with terminal.use_console(console):
        terminal.print_text("hello [x]")
    assert sink.getvalue() == "hello [x]\n"
    terminal.print_text("after")
    assert capsys.readouterr().out == "after\n"


def test_conftest_isolates_the_host_environment() -> None:
    assert terminal.is_dumb()
    assert not terminal.is_interactive(terminal.console_for())
