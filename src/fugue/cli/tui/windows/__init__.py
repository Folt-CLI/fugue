"""The windows of the work screen. Each one is a `Window`."""

from fugue.cli.tui.windows.checklist import ChecklistWindow
from fugue.cli.tui.windows.log import LogWindow
from fugue.cli.tui.windows.ticket import TicketWindow
from fugue.cli.tui.windows.window import Window

__all__ = ["ChecklistWindow", "LogWindow", "TicketWindow", "Window"]
