"""Choice prompts select, cancel and degrade to numbered line input."""

import os
import shutil
import sys
from io import StringIO

import pytest
import questionary
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
    monkeypatch.delenv("NO_COLOR", raising=False)
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
        monkeypatch.setattr(questionary, "select", fail)
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
        ("\x1b[A\r", 0, 1),
        ("\x1b[B\x1b[B\r", 0, 0),
    ],
)
def test_prompt_selection(keys: str, default: int, expected: int | None) -> None:
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        chooser = choices.PromptChooser(input=pipe, output=DummyOutput())
        assert chooser.choose("Pick", ["first", "second"], default) == expected


def test_prompt_ctrl_c_interrupts_choice() -> None:
    with create_pipe_input() as pipe:
        pipe.send_text("\x03\r")
        output = StringIO()
        chooser = choices.PromptChooser(
            stdin=StringIO("2\n"), stdout=output, input=pipe, output=DummyOutput()
        )
        with pytest.raises(KeyboardInterrupt):
            chooser.choose("Pick", ["first", "second"])
        assert output.getvalue() == ""


@pytest.mark.parametrize("default", [False, True])
def test_prompt_ctrl_c_interrupts_confirmation(default: bool) -> None:
    with create_pipe_input() as pipe:
        pipe.send_text("\x03\r")
        output = StringIO()
        chooser = choices.PromptChooser(
            stdin=StringIO("1\n"), stdout=output, input=pipe, output=DummyOutput()
        )
        with pytest.raises(KeyboardInterrupt):
            chooser.confirm("Continue?", default)
        assert output.getvalue() == ""


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


@pytest.mark.parametrize("prompt", [False, True])
def test_recommended_default_and_description(prompt: bool) -> None:
    output = StringIO()
    items = [choices.ChoiceItem("first"), choices.ChoiceItem("second", "A reason.\nMore.", True)]
    with create_pipe_input() as pipe:
        pipe.send_text("\r")
        chooser = (
            choices.PromptChooser(stdout=output, input=pipe, output=DummyOutput())
            if prompt
            else choices.LineChooser(stdin=StringIO("\n"), stdout=output)
        )
        assert chooser.choose("Pick", items) == 1
    assert "second [recommended]" in output.getvalue()
    assert "   A reason.\n   More.\n" in output.getvalue()
    assert "\x1b" not in output.getvalue()


@pytest.mark.parametrize(
    ("answer", "defaults", "allow_empty", "expected"),
    [
        ("1,3\n", (), False, [0, 2]),
        ("3, 1,3\n", (), False, [0, 2]),
        ("\n", (1,), False, [1]),
        ("\n", (), True, []),
        ("\n1\n", (), False, [0]),
        ("0\n4\n1,,3\nword\n1,2\n", (), False, [0, 1]),
        ("q\n", (), False, None),
        ("", (), False, None),
    ],
)
def test_line_choose_many(
    answer: str, defaults: tuple[int, ...], allow_empty: bool, expected: list[int] | None
) -> None:
    output = StringIO()
    chooser = choices.LineChooser(stdin=StringIO(answer), stdout=output)
    assert (
        chooser.choose_many(
            "Pick",
            [choices.ChoiceItem("first", "Details."), "second", "third"],
            defaults,
            allow_empty=allow_empty,
        )
        == expected
    )
    assert "1. first\n   Details.\n" in output.getvalue()


@pytest.mark.parametrize(
    ("keys", "defaults", "allow_empty", "expected"),
    [
        (" \x1b[B\x1b[B \r", (), False, [0, 2]),
        ("\r", (1,), False, [1]),
        ("\r", (), True, []),
        ("\r \r", (), False, [0]),
        (" \r", (0, 1), False, [1]),
        ("\x1b", (), False, None),
    ],
)
def test_prompt_choose_many(
    keys: str, defaults: tuple[int, ...], allow_empty: bool, expected: list[int] | None
) -> None:
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        chooser = choices.PromptChooser(input=pipe, output=DummyOutput())
        assert (
            chooser.choose_many(
                "Pick", ["first", "second", "third"], defaults, allow_empty=allow_empty
            )
            == expected
        )


def test_prompt_choose_many_ctrl_c_interrupts() -> None:
    with create_pipe_input() as pipe:
        pipe.send_text("\x03")
        output = StringIO()
        chooser = choices.PromptChooser(
            stdin=StringIO("1\n"), stdout=output, input=pipe, output=DummyOutput()
        )
        with pytest.raises(KeyboardInterrupt):
            chooser.choose_many("Pick", ["first"])
        assert output.getvalue() == ""


@pytest.mark.parametrize("prompt", [False, True])
def test_choose_many_recommended_default(prompt: bool) -> None:
    with create_pipe_input() as pipe:
        pipe.send_text("\r")
        chooser = (
            choices.PromptChooser(input=pipe, output=DummyOutput())
            if prompt
            else choices.LineChooser(stdin=StringIO("\n"), stdout=StringIO())
        )
        assert chooser.choose_many(
            "Pick", ["first", choices.ChoiceItem("second", recommended=True)]
        ) == [1]


@pytest.mark.parametrize("prompt", [False, True])
@pytest.mark.parametrize(("items", "defaults"), [([], ()), (["one"], (-1,)), (["one"], (1,))])
def test_invalid_multiple_choices(
    prompt: bool, items: list[str], defaults: tuple[int, ...]
) -> None:
    chooser = choices.PromptChooser() if prompt else choices.LineChooser()
    with pytest.raises(ValueError):
        chooser.choose_many("Pick", items, defaults)


@pytest.mark.parametrize("keys", ["y", "n", "\x1b"])
def test_prompt_confirmation(keys: str) -> None:
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        chooser = choices.PromptChooser(input=pipe, output=DummyOutput())
        assert chooser.confirm("Continue?", default=True) is (keys == "y")


@pytest.mark.parametrize("during_run", [False, True])
@pytest.mark.parametrize("kind", ["checkbox", "confirm"])
def test_other_library_start_failures_fall_back(
    monkeypatch: pytest.MonkeyPatch, during_run: bool, kind: str
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("terminal unavailable")

    if during_run:
        monkeypatch.setattr(Application, "run", fail)
    else:
        monkeypatch.setattr(questionary, kind, fail)
    with create_pipe_input() as pipe:
        chooser = choices.PromptChooser(
            stdin=StringIO("1,3\n" if kind == "checkbox" else "1\n"),
            stdout=StringIO(),
            input=pipe,
            output=DummyOutput(),
        )
        if kind == "checkbox":
            assert chooser.choose_many("Pick", ["first", "second", "third"]) == [0, 2]
        else:
            assert chooser.confirm("Continue?") is True


@pytest.mark.parametrize(
    ("width", "no_color", "prompt"), [(59, False, False), (60, False, True), (80, True, False)]
)
def test_environment_fallbacks(
    monkeypatch: pytest.MonkeyPatch, width: int, no_color: bool, prompt: bool
) -> None:
    monkeypatch.setattr(shutil, "get_terminal_size", lambda **kwargs: os.terminal_size((width, 24)))
    env = {"TERM": "xterm"}
    if no_color:
        env["NO_COLOR"] = ""
    chooser = choices.make_chooser(stdin=TerminalStream(), stdout=TerminalStream(), env=env)
    assert type(chooser) is (choices.PromptChooser if prompt else choices.LineChooser)
