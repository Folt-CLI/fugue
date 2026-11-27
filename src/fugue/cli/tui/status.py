"""The bar at the bottom: what the run is doing now."""

from rich.text import Text
from textual.widgets import Static


class StatusBar(Static):
    """One line with the current step, and how the run ended."""

    def __init__(self) -> None:
        super().__init__("Starting...")

    def running(self, step: str, position: int, total: int) -> None:
        where = f" · step {position} of {total}" if position else ""
        self.update(Text.assemble(("▶ ", "bold cyan"), (step, "bold"), (where, "dim")))

    def finished(self) -> None:
        self.update(Text.assemble(("✓ ", "bold green"), ("Done", "bold")))

    def paused(self, step: str) -> None:
        self.update(
            Text.assemble(("⏸ ", "bold yellow"), ("Paused", "bold"), (f" · before {step}", "dim"))
        )

    def stopped(self, message: str) -> None:
        self.update(
            Text.assemble(("■ ", "bold yellow"), ("Stopped", "bold"), (f" · {message}", "dim"))
        )

    def failed(self, message: str) -> None:
        self.update(
            Text.assemble(("✗ ", "bold red"), ("Failed", "bold red"), (f" · {message}", "dim"))
        )
