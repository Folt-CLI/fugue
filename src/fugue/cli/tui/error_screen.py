"""The window that says a run failed: its own screen over the main one."""

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from fugue.cli.views.markdown import MarkdownBody


class ErrorScreen(ModalScreen[None]):
    """Opens when the run ends with an error, so it is never missed in the STDERR window. It
    closes on Enter or Escape; the main screen stays open behind it to be read, and `q` quits."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "close", "Close"),
        Binding("escape", "close", "Close", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="error-window"):
            yield Static(MarkdownBody(self._message), id="error-text")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#error-window").border_title = "The run failed"

    def action_close(self) -> None:
        self.dismiss()

    async def action_quit(self) -> None:
        await self.app.action_quit()
