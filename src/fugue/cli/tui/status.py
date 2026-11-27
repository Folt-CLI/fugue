"""The bar at the bottom: what the run is doing now."""

from rich.text import Text
from textual.widgets import Static


class StatusBar(Static):
    """One line with the state of the run: Running, Stopped or Finished."""

    def __init__(self) -> None:
        super().__init__("Starting...")

    def running(self, step: str, position: int, total: int) -> None:
        where = f" · step {position} of {total}" if position else ""
        self.update(Text.assemble(("▶ ", "bold cyan"), (step, "bold"), (where, "dim")))

    def resumed(self) -> None:
        self.update(Text.assemble(("▶ ", "bold cyan"), ("Running", "bold")))

    def stopped(self, message: str, *, resumable: bool = False) -> None:
        """Stopped, with why. A run that can be continued says which key does it."""
        line = Text.assemble(
            ("■ ", "bold yellow"),
            ("Stopped", "bold"),
            (f" · {message.removeprefix('Stopped ')}", "dim"),
        )
        if resumable:
            line.append_text(
                Text.assemble((" · press ", "dim"), ("r", "bold yellow"), (" to continue", "dim"))
            )
        self.update(line)

    def finished(self) -> None:
        self.update(Text.assemble(("✓ ", "bold green"), ("Finished", "bold")))
