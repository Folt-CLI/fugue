"""Markdown with its diagrams drawn.

A ticket or a question body is Markdown, and it can hold Mermaid diagrams in fenced blocks. GitHub
draws those itself, from the same text. A terminal cannot, so they are drawn here as text with
`termaid`, and everything else goes through Rich's Markdown as before.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar

from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from termaid import render, type_problem


@dataclass(frozen=True)
class Drawing:
    """A diagram drawn as text, or the reason it is not."""

    art: str | None
    note: str = ""


class Diagram:
    """A Mermaid diagram as text that fits a width.

    It is drawn compactly first, with no empty lines inside the boxes, because a terminal is
    short. When that is too wide, the spacing and the labels are tightened step by step. A
    diagram that still does not fit, or that cannot be read, gives `None`: squeezing it further
    only garbles the lines, so the caller shows its source instead.
    """

    ATTEMPTS: ClassVar[tuple[tuple[int, int, int | None], ...]] = (
        (2, 2, None),
        (2, 2, 14),
        (1, 1, 12),
        (1, 1, 10),
    )
    """The gap, the horizontal padding and the widest label to try, in this order."""

    _drawn: ClassVar[dict[tuple[str, int], Drawing]] = {}

    def __init__(self, source: str) -> None:
        self._source = source

    def draw(self, width: int) -> Drawing:
        key = (self._source, width)
        if key not in self._drawn:
            self._drawn[key] = self._fit(width)
        return self._drawn[key]

    def _fit(self, width: int) -> Drawing:
        problem = type_problem(self._source)
        if problem is not None:
            return Drawing(None, f"The terminal cannot draw this diagram ({problem.rstrip('.')}).")
        for gap, padding, label in self.ATTEMPTS:
            try:
                art = render(
                    self._source, gap=gap, padding_x=padding, padding_y=0, max_label_width=label
                ).rstrip("\n")
            except Exception as error:  # a diagram termaid cannot read is shown as its source
                return Drawing(None, f"The terminal cannot draw this diagram ({error}).")
            if art and max(len(line) for line in art.splitlines()) <= width:
                return Drawing(art)
        return Drawing(None, "This diagram is wider than the window.")  # squeezing garbles it


class MarkdownBody:
    """Markdown whose Mermaid blocks are drawn as diagrams.

    It is a Rich renderable, so it is laid out again at the width it is shown at, and a diagram
    follows a window being resized.
    """

    FENCE: ClassVar[re.Pattern[str]] = re.compile(
        r"^```mermaid[ \t]*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL
    )

    SOURCE_NOTE: ClassVar[str] = (
        " It is shown as its source. Hide the checklist with c, or read it on GitHub."
    )

    def __init__(self, text: str) -> None:
        self._text = text

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        for kind, part in self._parts():
            if kind == "prose":
                if part.strip():
                    yield Markdown(part)
                continue
            drawing = Diagram(part).draw(options.max_width)
            if drawing.art is None:
                yield Syntax(part, "text", theme="ansi_dark", word_wrap=True)
                yield Text(drawing.note + self.SOURCE_NOTE, style="dim")
            else:
                yield Text(drawing.art, no_wrap=True, overflow="ignore")

    def _parts(self) -> Iterator[tuple[str, str]]:
        """The text in order, as prose and Mermaid sources."""
        at = 0
        for found in self.FENCE.finditer(self._text):
            yield "prose", self._text[at : found.start()]
            yield "diagram", found.group(1)
            at = found.end()
        yield "prose", self._text[at:]
