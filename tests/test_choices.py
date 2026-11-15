"""Choice prompts select, cancel and degrade to numbered line input."""

import sys
from io import StringIO

import pytest
from prompt_toolkit.application import Application
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from fugue.cli import choices


@pytest.mark.parametrize(
    ("answer", "default", "expected"),
    [("2\n", 0, 1), ("\n", 1, 1), ("q\n", 0, None), (" Q \n", 0, None), ("", 0, None)],
)
def test_line_selection(answer: str, default: int, expected: int | None) -> None:
    chooser = choices.LineChooser(stdin=StringIO(answer), stdout=StringIO())
    assert chooser.choose("Pick", ["first", "second"], default) == expected


def test_line_retries_invalid_input() -> None:
    output = StringIO()
    chooser = choices.LineChooser(stdin=StringIO("word\n0\n3\n-1\n1.5\n2\n"), stdout=output)
    assert chooser.choose("Pick", ["first", "second"]) == 1
    assert output.getvalue().count("Enter a number from 1 to 2") == 5
    assert "1. first\n2. second\n" in output.getvalue()
    assert "\x1b" not in output.getvalue()


@pytest.mark.parametrize("default", [False, True])
def test_confirm_default(default: bool) -> None:
    chooser = choices.LineChooser(stdin=StringIO("\n"), stdout=StringIO())
    assert chooser.confirm("Continue?", default) is default


@pytest.mark.parametrize(("answer", "expected"), [("1\n", True), ("2\n", False), ("q\n", False)])
def test_confirm_explicit_answer(answer: str, expected: bool) -> None:
    chooser = choices.LineChooser(stdin=StringIO(answer), stdout=StringIO())
    assert chooser.confirm("Continue?", default=True) is expected


@pytest.mark.parametrize("prompt", [False, True])
@pytest.mark.parametrize(("labels", "default"), [([], 0), (["one"], -1), (["one"], 1)])
def test_invalid_choices(prompt: bool, labels: list[str], default: int) -> None:
    chooser = choices.PromptChooser() if prompt else choices.LineChooser()
    with pytest.raises(ValueError):
        chooser.choose("Pick", labels, default)


class TerminalStream(StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("input_terminal", "output_terminal", "term", "prompt"),
    [
        (True, True, "xterm", True),
        (False, True, "xterm", False),
        (True, False, "xterm", False),
        (False, False, "xterm", False),
        (True, True, "dumb", False),
    ],
)
def test_make_chooser(input_terminal: bool, output_terminal: bool, term: str, prompt: bool) -> None:
    source = TerminalStream() if input_terminal else StringIO()
    target = TerminalStream() if output_terminal else StringIO()
    chooser = choices.make_chooser(stdin=source, stdout=target, env={"TERM": term})
    assert type(chooser) is (choices.PromptChooser if prompt else choices.LineChooser)


def test_make_chooser_uses_current_streams_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", TerminalStream())
    monkeypatch.setattr(sys, "stdout", TerminalStream())
    monkeypatch.setenv("TERM", "dumb")
    assert type(choices.make_chooser()) is choices.LineChooser
    monkeypatch.setenv("TERM", "xterm")
    assert isinstance(choices.make_chooser(), choices.PromptChooser)


def test_library_construction_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**kwargs: object) -> None:
        raise RuntimeError("terminal unavailable")

    monkeypatch.setattr(choices, "PromptChooser", fail)
    chooser = choices.make_chooser(
        stdin=TerminalStream("2\n"), stdout=TerminalStream(), env={"TERM": "xterm"}
    )
    assert type(chooser) is choices.LineChooser
    assert chooser.choose("Pick", ["first", "second"]) == 1


@pytest.mark.parametrize("during_run", [False, True])
def test_library_start_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch, during_run: bool
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("terminal unavailable")

    if during_run:
        monkeypatch.setattr(Application, "run", fail)
    else:
        monkeypatch.setattr(choices, "Application", fail)
    with create_pipe_input() as pipe:
        chooser = choices.PromptChooser(
            stdin=StringIO("2\n"), stdout=StringIO(), input=pipe, output=DummyOutput()
        )
        assert chooser.choose("Pick", ["first", "second"]) == 1


@pytest.mark.parametrize(
    ("keys", "default", "expected"),
    [
        ("\x1b[B\r", 0, 1),
        ("\x1b", 0, None),
        ("\r", 1, 1),
        ("\x1b[A\r", 1, 0),
        ("\x1b[A\r", 0, 0),
        ("\x1b[B\x1b[B\r", 0, 1),
    ],
)
def test_prompt_selection(keys: str, default: int, expected: int | None) -> None:
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        chooser = choices.PromptChooser(input=pipe, output=DummyOutput())
        assert chooser.choose("Pick", ["first", "second"], default) == expected


def test_prompt_eof_cancels() -> None:
    with create_pipe_input() as pipe:
        pipe.close()
        chooser = choices.PromptChooser(input=pipe, output=DummyOutput())
        assert chooser.choose("Pick", ["first", "second"]) is None


@pytest.mark.parametrize("default", [False, True])
def test_prompt_confirm_default(default: bool) -> None:
    with create_pipe_input() as pipe:
        pipe.send_text("\r")
        chooser = choices.PromptChooser(input=pipe, output=DummyOutput())
        assert chooser.confirm("Continue?", default) is default
