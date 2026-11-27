"""The small floating window that asks the person running the flow for an action."""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from fugue.core.reporting import Question


class QuestionScreen(ModalScreen[str]):
    """Floats over whichever window is showing, with one button per choice. A key that starts
    a choice picks it. Closes with the chosen answer."""

    def __init__(self, question: Question) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._question.text, id="question")
            with Horizontal(id="choices"):
                for choice in self._question.choices:
                    yield Button(
                        choice,
                        name=choice,
                        variant="primary" if choice == self._question.default else "default",
                    )

    def on_mount(self) -> None:
        self.query_one("#dialog").border_title = "Action needed"
        for button in self.query(Button):
            if button.name == self._question.default:
                button.focus()

    def on_key(self, event: Key) -> None:
        for choice in self._question.choices:
            if event.character and choice.lower().startswith(event.character.lower()):
                event.stop()
                self.dismiss(choice)
                return

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.name or self._question.default)
