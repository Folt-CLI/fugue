"""Choice prompt port with terminal and line-based implementations."""

import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Protocol, TextIO, TypeVar, cast

import questionary
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import Input, create_input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent, merge_key_bindings
from prompt_toolkit.output import Output, create_output
from questionary import Question


@dataclass(frozen=True)
class ChoiceItem:
    """A labelled choice with optional explanatory text and recommendation."""

    label: str
    description: str = ""
    recommended: bool = False


class Chooser(Protocol):
    """A choice prompt returns zero-based indexes, or None on cancellation."""

    def choose(
        self, message: str, choices: Sequence[str | ChoiceItem], default: int = 0
    ) -> int | None: ...

    def choose_many(
        self,
        message: str,
        items: Sequence[str | ChoiceItem],
        defaults: Sequence[int] = (),
        *,
        allow_empty: bool = False,
    ) -> list[int] | None: ...

    def confirm(self, message: str, default: bool = False) -> bool: ...


def _items(choices: Sequence[str | ChoiceItem]) -> list[ChoiceItem]:
    if not choices:
        raise ValueError("choices must not be empty")
    return [ChoiceItem(choice) if isinstance(choice, str) else choice for choice in choices]


def _default(items: Sequence[ChoiceItem], default: int) -> int:
    if not 0 <= default < len(items):
        raise ValueError("default must be a valid choice index")
    return next((index for index, item in enumerate(items) if item.recommended), default)


def _defaults(items: Sequence[ChoiceItem], defaults: Sequence[int]) -> list[int]:
    if any(not 0 <= index < len(items) for index in defaults):
        raise ValueError("defaults must be valid choice indexes")
    selected = (
        set(defaults)
        if defaults
        else {index for index, item in enumerate(items) if item.recommended}
    )
    return [index for index in range(len(items)) if index in selected]


def _label(item: ChoiceItem) -> str:
    return f"{item.label} [recommended]" if item.recommended else item.label


class LineChooser:
    """Numbered choice prompts for streams without terminal control."""

    def __init__(self, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        self._stdin = sys.stdin if stdin is None else stdin
        self._stdout = sys.stdout if stdout is None else stdout

    def _print_items(self, message: str, items: Sequence[ChoiceItem]) -> None:
        print(message, file=self._stdout)
        for index, item in enumerate(items, 1):
            print(f"{index}. {_label(item)}", file=self._stdout)
            for line in item.description.splitlines():
                print(f"   {line}", file=self._stdout)

    def _read(self, message: str) -> str | None:
        print(message, end="", file=self._stdout, flush=True)
        try:
            line = self._stdin.readline()
        except EOFError:
            return None
        if not line or line.strip().lower() == "q":
            return None
        return line.strip()

    def choose(
        self, message: str, choices: Sequence[str | ChoiceItem], default: int = 0
    ) -> int | None:
        items = _items(choices)
        default = _default(items, default)
        self._print_items(message, items)
        while True:
            answer = self._read(f"Choice [{default + 1}] (q to cancel): ")
            if answer is None:
                return None
            if not answer:
                return default
            if answer.isascii() and answer.isdecimal() and 1 <= int(answer) <= len(items):
                return int(answer) - 1
            print(f"Enter a number from 1 to {len(items)}, or q to cancel.", file=self._stdout)

    def choose_many(
        self,
        message: str,
        items: Sequence[str | ChoiceItem],
        defaults: Sequence[int] = (),
        *,
        allow_empty: bool = False,
    ) -> list[int] | None:
        entries = _items(items)
        selected = _defaults(entries, defaults)
        self._print_items(message, entries)
        hint = ",".join(str(index + 1) for index in selected)
        while True:
            answer = self._read(f"Choices [{hint}] (comma-separated numbers, q to cancel): ")
            if answer is None:
                return None
            if not answer:
                if selected or allow_empty:
                    return selected
            else:
                parts = [part.strip() for part in answer.split(",")]
                if all(
                    part.isascii() and part.isdecimal() and 1 <= int(part) <= len(entries)
                    for part in parts
                ):
                    indexes = {int(part) - 1 for part in parts}
                    return [index for index in range(len(entries)) if index in indexes]
            print(
                f"Enter numbers from 1 to {len(entries)} separated by commas, or q to cancel.",
                file=self._stdout,
            )

    def confirm(self, message: str, default: bool = False) -> bool:
        return self.choose(message, ("Yes", "No"), default=0 if default else 1) == 0


_Result = TypeVar("_Result")


class PromptChooser(LineChooser):
    """Library choice prompts with line fallback when terminal startup fails."""

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

    def _run(self, factory: Callable[[], Question], fallback: Callable[[], _Result]) -> _Result:
        try:
            with ExitStack() as stack:
                source = self._input
                if source is None:
                    source = create_input(stdin=self._stdin)
                    stack.callback(source.close)
                target = self._output
                if target is None:
                    target = create_output(stdout=self._stdout)
                with create_app_session(input=source, output=target):
                    question = factory()
                    bindings = KeyBindings()

                    @bindings.add("escape")
                    def cancel(event: KeyPressEvent) -> None:
                        event.app.exit(result=None)

                    original = question.application.key_bindings
                    question.application.key_bindings = merge_key_bindings(
                        [bindings] if original is None else [original, bindings]
                    )
                    # unsafe_ask preserves KeyboardInterrupt instead of swallowing it.
                    return cast(_Result, question.unsafe_ask())
        except EOFError:
            return cast(_Result, None)
        except Exception:
            return fallback()

    def _descriptions(self, items: Sequence[ChoiceItem]) -> None:
        # Questionary has no per-choice description field.
        for item in items:
            if item.description:
                print(f"{_label(item)}:", file=self._stdout)
                for line in item.description.splitlines():
                    print(f"   {line}", file=self._stdout)

    def choose(
        self, message: str, choices: Sequence[str | ChoiceItem], default: int = 0
    ) -> int | None:
        items = _items(choices)
        default = _default(items, default)
        self._descriptions(items)
        options = [questionary.Choice(_label(item), value=i) for i, item in enumerate(items)]
        return self._run(
            lambda: questionary.select(
                message,
                choices=options,
                default=options[default],
                use_indicator=False,
                use_arrow_keys=True,
            ),
            lambda: super(PromptChooser, self).choose(message, choices, default),
        )

    def choose_many(
        self,
        message: str,
        items: Sequence[str | ChoiceItem],
        defaults: Sequence[int] = (),
        *,
        allow_empty: bool = False,
    ) -> list[int] | None:
        entries = _items(items)
        selected = _defaults(entries, defaults)
        self._descriptions(entries)
        return self._run(
            lambda: questionary.checkbox(
                message,
                choices=[
                    questionary.Choice(_label(item), value=i, checked=i in selected)
                    for i, item in enumerate(entries)
                ],
                validate=lambda values: (
                    allow_empty or bool(values) or "Select at least one choice."
                ),
            ),
            lambda: super(PromptChooser, self).choose_many(
                message, items, defaults, allow_empty=allow_empty
            ),
        )

    def confirm(self, message: str, default: bool = False) -> bool:
        result = self._run(
            lambda: questionary.confirm(message, default=default),
            lambda: LineChooser(stdin=self._stdin, stdout=self._stdout).confirm(message, default),
        )
        return result is True


def make_chooser(
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    env: Mapping[str, str] | None = None,
) -> Chooser:
    """Use terminal prompts only when both streams and the environment support them."""
    source = sys.stdin if stdin is None else stdin
    target = sys.stdout if stdout is None else stdout
    settings = os.environ if env is None else env
    if (
        not source.isatty()
        or not target.isatty()
        or settings.get("TERM") == "dumb"
        or "NO_COLOR" in settings
        or shutil.get_terminal_size(fallback=(80, 24)).columns < 60
    ):
        return LineChooser(stdin=source, stdout=target)
    try:
        return PromptChooser(stdin=source, stdout=target)
    except Exception:
        return LineChooser(stdin=source, stdout=target)
