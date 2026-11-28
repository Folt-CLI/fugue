"""The windows that show what is written while a run goes on: one per stream. The agents'
own window is in `ai.py`."""

from rich.console import RenderableType
from rich.text import Text

from fugue.cli.tui.windows.window import Window


class LogWindow(Window):
    """A window that collects lines, one stream each. It is a base: a subclass is one stream,
    and says how its lines look."""

    style: str = ""

    async def write(self, line: RenderableType) -> None:
        """Add a line; plain text takes this window's style."""
        await self.add(Text(line, style=self.style) if isinstance(line, str) else line)


class StdoutWindow(LogWindow):
    """What the commands a flow runs print, and what Fugue itself reports as it goes."""

    title = "STDOUT"
    key = "o"
    placeholder = "Nothing yet."


class StderrWindow(LogWindow):
    """What commands print on their error stream, and Fugue's warnings and failures."""

    title = "STDERR"
    key = "e"
    placeholder = "No errors."
    style = "red"
