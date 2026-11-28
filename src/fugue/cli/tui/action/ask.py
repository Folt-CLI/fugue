"""Pick one option, or write another."""

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import ItemGrid
from textual.events import Key
from textual.widgets import Button, Input

from fugue.cli.tui.action.base import Action
from fugue.cli.tui.action.other import OtherField, OtherInput
from fugue.core.reporting import Answer, Ask


class AskAction(Action[Ask]):
    """The options as buttons that wrap onto more rows, and the Other field under them.

    Left and right move between the buttons and Enter picks one; the letter an option starts
    with picks it too. Down goes to the Other field and Enter there answers with its text.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left", "choose(-1)", "Choose", key_display="←→"),
        Binding("right", "choose(1)", "Choose", show=False),
        Binding("down", "other", "Other", show=False),
    ]

    def compose(self) -> ComposeResult:
        widest = max(len(option) for option in self.question.options) + 8
        yield ItemGrid(
            *(Button(self._label(option), name=option) for option in self.question.options),
            id="action-choices",
            min_column_width=max(widest, 14),
            max_column_width=max(widest, 14) + 10,
        )
        if self.question.other:
            yield OtherField(self.question.other)

    def _label(self, option: str) -> Text:
        """An option with the letter that picks it underlined, if no option before it has it."""
        first = next(o for o in self.question.options if o[:1].lower() == option[:1].lower())
        if first != option:
            return Text(option)
        return Text.assemble((option[:1], "bold underline"), option[1:])

    def focus_first(self) -> None:
        buttons = list(self.query(Button))
        wanted = [b for b in buttons if b.name == self.question.default] or buttons
        if wanted:
            wanted[0].focus()

    def action_choose(self, step: int) -> None:
        """Move to the previous or next button, wrapping around."""
        buttons = list(self.query(Button))
        focused = self.app.focused
        if not buttons:
            return
        index = buttons.index(focused) if focused in buttons else 0
        buttons[(index + step) % len(buttons)].focus()

    def action_other(self) -> None:
        for field in self.query(OtherField):
            field.input.focus()

    def on_other_input_leave(self, event: OtherInput.Leave) -> None:
        event.stop()
        if event.direction < 0:
            self.focus_first()

    def commit(self, answer: Answer) -> None:
        super().commit(answer)
        if self.embedded:  # in a form the picked option stays marked
            for button in self.query(Button):
                button.set_class(not answer.other and button.name in answer.values, "-picked")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.commit(Answer(values=(event.button.name or self.question.default,)))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        text = event.value.strip()
        if text:
            self.commit(Answer(values=(text,), other=True))

    def on_key(self, event: Key) -> None:
        """The first letter of an option picks it, while a button has focus."""
        if not event.character or not isinstance(self.app.focused, Button):
            return
        for option in self.question.options:
            if option.lower().startswith(event.character.lower()):
                event.stop()
                event.prevent_default()
                self.commit(Answer(values=(option,)))
                return
