"""The window a form is filled in: its own screen over the main one."""

from rich.markdown import Markdown
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from fugue.cli.tui.action import Action, FormAction
from fugue.core.reporting import Answer, Form


class FormScreen(ModalScreen[Answer]):
    """A form is too big for the strip at the bottom, so it opens in a window of its own that
    covers most of the screen. It closes with the form's answer when it is submitted."""

    def __init__(self, form: Form) -> None:
        super().__init__()
        self._form = form

    def compose(self) -> ComposeResult:
        with Vertical(id="form-window"):
            if self._form.body:
                yield Static(Markdown(self._form.body), id="form-intro")
            yield FormAction(self._form)
        yield Footer()

    def on_mount(self) -> None:
        asker = f"{self._form.asked_by}: " if self._form.asked_by else ""
        self.query_one("#form-window").border_title = f"{asker}{self._form.text}"
        self.call_after_refresh(self.query_one(FormAction).focus_first)

    def on_action_answered(self, event: Action.Answered) -> None:
        event.stop()
        self.dismiss(event.answer)
