"""Terminal layer: styled status, tables, panels, Markdown and progress.

Every command prints through this module. Output degrades to plain text when it
is piped, captured, or the environment asks for no colour (NO_COLOR, TERM=dumb).
Tables, panels and Markdown also use plain text below 60 columns.
"""

import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from io import StringIO
from types import TracebackType
from typing import Self

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# Columns whose cells are identifiers; they must never be cut short.
ID_COLUMNS = frozenset({"id", "run", "ticket", "index"})

MIN_RICH_WIDTH = 60

_PREFIX_STYLES = {
    "error:": "bold red",
    "warning:": "yellow",
    "problem:": "yellow",
    "next:": "cyan",
    "note:": "dim",
}

THEME = {
    "badge": "bold cyan",
    "panel": "cyan",
    "summary.title": "bold",
    "summary.recommended": "bold green",
    "progress": "cyan",
}

# The console of the command in progress, so a live display and every print share one.
_active: Console | None = None


def _environment(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def is_dumb(env: Mapping[str, str] | None = None) -> bool:
    return _environment(env).get("TERM", "") == "dumb"


def _columns(env: Mapping[str, str]) -> int | None:
    value = env.get("COLUMNS", "")
    return int(value) if value.isascii() and value.isdecimal() and int(value) > 0 else None


def console_for(
    env: Mapping[str, str] | None = None, *, stderr: bool = False, file: StringIO | None = None
) -> Console:
    """Build a console from the environment; one per command."""
    settings = _environment(env)
    dumb = is_dumb(settings)
    no_color = dumb or bool(settings.get("NO_COLOR"))
    return Console(
        file=file,
        stderr=stderr,
        width=_columns(settings),
        no_color=no_color,
        color_system=None if dumb else "auto",
        highlight=False,
        emoji=False,
        legacy_windows=False,
        theme=Theme(THEME),
        _environ=dict(settings),
    )


def is_interactive(console: Console) -> bool:
    """True on a real, non-dumb terminal where live displays make sense."""
    return console.is_terminal and not console.is_dumb_terminal


def supports_rich(console: Console) -> bool:
    """True when styled tables, panels and Markdown fit a colour terminal."""
    return is_interactive(console) and not console.no_color and console.width >= MIN_RICH_WIDTH


def _current() -> Console:
    return _active if _active is not None else console_for()


@contextmanager
def use_console(console: Console) -> Iterator[Console]:
    """Make console the one every helper prints to while the block runs."""
    global _active
    previous, _active = _active, console
    try:
        yield console
    finally:
        _active = previous


def styled_text(text: str) -> Text:
    """Literal text; a recognised prefix such as `error:` is styled, never parsed as markup."""
    result = Text(text)
    for prefix, style in _PREFIX_STYLES.items():
        if text.startswith(prefix):
            result.stylize(style, 0, len(prefix))
            break
    return result


def print_text(text: str, *, console: Console | None = None) -> None:
    """Print literal text: brackets such as [roles.x] stay as written."""
    (console or _current()).print(styled_text(text), soft_wrap=True)


def status_line(text: str, *, console: Console | None = None) -> None:
    print_text(text, console=console)


def format_error(message: str, next_step: str | None = None) -> list[str]:
    """The `error:` line, then a `next:` line when a next step is known."""
    lines = [f"error: {message}"]
    if next_step:
        lines.append(f"next: {next_step}")
    return lines


def error(message: str, next_step: str | None = None) -> None:
    """`error: message` on stderr, then `next: ...` when a next step is known."""
    err = console_for(stderr=True)
    for line in format_error(message, next_step):
        print_text(line, console=err)


def deprecated_name(old: str, new: str, removed_in: str) -> None:
    """Print one warning on stderr naming the replacement and removal release."""
    print_text(
        f"warning: {old} is deprecated; use {new} instead. It will be removed in {removed_in}.",
        console=console_for(stderr=True),
    )


def table_text(
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    id_columns: frozenset[str] = ID_COLUMNS,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Plain aligned lines; wraps to COLUMNS without cutting identifier columns."""
    table = _build_table(header, rows, id_columns)
    sink = StringIO()
    # Always colourless: the lines are text that callers print later.
    console = Console(
        file=sink,
        width=console_for(env).width,
        color_system=None,
        force_terminal=False,
        highlight=False,
        emoji=False,
        _environ={},
    )
    console.print(table, soft_wrap=False)
    return [line.rstrip() for line in sink.getvalue().splitlines()]


def _build_table(
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    id_columns: frozenset[str],
    *,
    title: str | None = None,
    plain: bool = True,
) -> Table:
    table = Table(
        title=title,
        box=None if plain else box.SIMPLE_HEAD,
        padding=(0, 2) if plain else (0, 1),
        collapse_padding=plain,
        pad_edge=not plain,
        show_edge=not plain,
    )
    for name in header:
        whole = name in id_columns
        table.add_column(name, no_wrap=whole, overflow="fold", min_width=len(name) if whole else 1)
    for row in rows:
        table.add_row(*(Text(cell) for cell in row))
    return table


def print_table(
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    title: str | None = None,
    console: Console | None = None,
) -> None:
    target = console or _current()
    if supports_rich(target):
        target.print(_build_table(header, rows, ID_COLUMNS, title=title, plain=False))
        return
    if title:
        print_text(title, console=target)
    for line in table_text(header, rows, env=_env_for(target)):
        print_text(line, console=target)


def _env_for(console: Console) -> Mapping[str, str]:
    return {**os.environ, "COLUMNS": str(console.width)}


def print_panel(title: str, body: str, *, console: Console | None = None) -> None:
    target = console or _current()
    if supports_rich(target):
        target.print(Panel(Text(body), title=Text(title), border_style=THEME["panel"]))
        return
    target.print(Text(title), soft_wrap=True)
    target.print(Text(body), soft_wrap=True)


def print_badge(label: str, *, console: Console | None = None) -> None:
    """Print a literal bracketed label, styled only when Rich is enabled."""
    target = console or _current()
    style = THEME["badge"] if supports_rich(target) else ""
    target.print(Text(f"[{label}]", style=style), soft_wrap=True)


def print_summary(
    title: str,
    overview: str,
    options: Sequence[str],
    *,
    recommended_index: int | None = None,
    console: Console | None = None,
) -> None:
    """Print the first three overview lines and numbered option names.

    The optional recommended index is zero-based. Invalid indices raise before
    any output is printed; an empty option list is allowed without a recommendation.
    """
    if recommended_index is not None and not 0 <= recommended_index < len(options):
        raise ValueError("recommended_index must identify an option")
    target = console or _current()
    rich = supports_rich(target)
    body = Text()
    lines = overview.splitlines()
    body.append("\n".join(lines[:3]))
    if len(lines) > 3:
        body.append("\n...")
    for index, name in enumerate(options):
        if body:
            body.append("\n")
        recommended = index == recommended_index
        suffix = " (recommended)" if recommended else ""
        style = THEME["summary.recommended"] if rich and recommended else ""
        body.append(f"{index + 1}. {name}{suffix}", style=style)
    if rich:
        target.print(
            Panel(
                body,
                title=Text(title, style=THEME["summary.title"]),
                border_style=THEME["panel"],
            )
        )
    else:
        target.print(Text(title), soft_wrap=True)
        if body:
            target.print(body, soft_wrap=True)


def print_markdown(text: str, *, console: Console | None = None) -> None:
    target = console or _current()
    if supports_rich(target):
        target.print(Markdown(text))
    else:
        print_text(text, console=target)


class Progress:
    """Progress callback: a live spinner for one run on a terminal, plain lines otherwise.

    Use as a context manager around the run. A batch never uses the spinner, so lines
    stay whole and prefixed by their caller.
    """

    def __init__(
        self,
        *,
        batch: bool = False,
        env: Mapping[str, str] | None = None,
        console: Console | None = None,
    ) -> None:
        self._console = console or _active or console_for(env)
        self._live = not batch and supports_rich(self._console)
        self._status: Status | None = None
        self._previous: Console | None = None

    @property
    def live(self) -> bool:
        return self._live

    def __call__(self, line: str) -> None:
        if self._status is not None:
            self._status.update(Text(line))
        else:
            self._console.print(Text(line), soft_wrap=True)

    def __enter__(self) -> Self:
        global _active
        if self._live:
            self._previous = _active
            _active = self._console
            self._status = self._console.status(
                "starting", spinner="dots", spinner_style=THEME["progress"]
            )
            self._status.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        global _active
        if self._status is not None:
            self._status.stop()
            self._status = None
            _active = self._previous


def progress_callback(*, batch: bool = False, console: Console | None = None) -> Progress:
    return Progress(batch=batch, console=console)
