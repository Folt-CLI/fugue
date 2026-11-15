"""Choice prompt port with terminal and line-based implementations."""

import os
import sys
from collections.abc import Mapping, Sequence
from typing import Protocol, TextIO

from prompt_toolkit.application import Application
from prompt_toolkit.input import Input, create_input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import Output, create_output


class Chooser(Protocol):
    """A choice prompt returns a zero-based index, or None on cancellation."""

    def choose(self, message: str, choices: Sequence[str], default: int = 0) -> int | None: ...

    def confirm(self, message: str, default: bool = False) -> bool: ...


def _validate(choices: Sequence[str], default: int) -> None:
    if not choices:
        raise ValueError("choices must not be empty")
    if not 0 <= default < len(choices):
        raise ValueError("default must be a valid choice index")


class LineChooser:
    """Numbered choice prompts for streams without terminal control."""

    def __init__(self, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        self._stdin = sys.stdin if stdin is None else stdin
        self._stdout = sys.stdout if stdout is None else stdout

    def choose(self, message: str, choices: Sequence[str], default: int = 0) -> int | None:
        _validate(choices, default)
        print(message, file=self._stdout)
        for index, label in enumerate(choices, 1):
            print(f"{index}. {label}", file=self._stdout)
        while True:
            print(f"Choice [{default + 1}] (q to cancel): ", end="", file=self._stdout, flush=True)
            try:
                line = self._stdin.readline()
            except EOFError:
                return None
            if not line:
                return None
            answer = line.strip()
            if answer.lower() == "q":
                return None
            if not answer:
                return default
            if answer.isascii() and answer.isdecimal() and 1 <= int(answer) <= len(choices):
                return int(answer) - 1
            print(f"Enter a number from 1 to {len(choices)}, or q to cancel.", file=self._stdout)

    def confirm(self, message: str, default: bool = False) -> bool:
        return self.choose(message, ("Yes", "No"), default=0 if default else 1) == 0


class PromptChooser(LineChooser):
    """A single-prompt application with arrow selection and line fallback."""

    def __init__(
        self,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        input: Input | None = None,
        output: Output | None = None,
    ) -> None:
        super().__init__(stdin=stdin, stdout=stdout)
        self._input = input
        self._output = output

    def choose(self, message: str, choices: Sequence[str], default: int = 0) -> int | None:
        _validate(choices, default)
        selected = default
        bindings = KeyBindings()

        @bindings.add("up")
        def up(event: KeyPressEvent) -> None:
            nonlocal selected
            selected = max(0, selected - 1)

        @bindings.add("down")
        def down(event: KeyPressEvent) -> None:
            nonlocal selected
            selected = min(len(choices) - 1, selected + 1)

        @bindings.add("enter")
        def accept(event: KeyPressEvent) -> None:
            event.app.exit(result=selected)

        @bindings.add("escape")
        def cancel(event: KeyPressEvent) -> None:
            event.app.exit(result=None)

        def text() -> str:
            entries = "\n".join(
                f"{'>' if index == selected else ' '} {label}"
                for index, label in enumerate(choices)
            )
            return f"{message}\n{entries}\nUp/Down to choose, Enter to select, Esc to cancel."

        try:
            application: Application[int | None] = Application(
                layout=Layout(Window(FormattedTextControl(text))),
                key_bindings=bindings,
                mouse_support=True,
                full_screen=False,
                input=self._input if self._input is not None else create_input(stdin=self._stdin),
                output=self._output
                if self._output is not None
                else create_output(stdout=self._stdout),
            )
            return application.run()
        except EOFError:
            return None
        except Exception:
            # Terminal setup can fail even after isatty() succeeded.
            return super().choose(message, choices, default)


def make_chooser(
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    env: Mapping[str, str] | None = None,
) -> Chooser:
    """Use terminal prompts only when both streams support them."""
    source = sys.stdin if stdin is None else stdin
    target = sys.stdout if stdout is None else stdout
    settings = os.environ if env is None else env
    if not source.isatty() or not target.isatty() or settings.get("TERM") == "dumb":
        return LineChooser(stdin=source, stdout=target)
    try:
        return PromptChooser(stdin=source, stdout=target)
    except Exception:
        return LineChooser(stdin=source, stdout=target)
