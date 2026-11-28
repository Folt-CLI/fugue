"""Pick any number of options, and write another."""

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widgets import Input, SelectionList

from fugue.cli.tui.action.base import Action
from fugue.cli.tui.action.other import OtherField, OtherInput
from fugue.core.reporting import Answer, Select


class Picker(SelectionList[str]):
    """The tick list. Enter confirms instead of toggling (space toggles); down from the last
    option moves on to the Other field."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "confirm", "Confirm"),
        Binding("space", "select", "Toggle"),
        Binding("down", "down", "Down", show=False),
    ]

    class Confirm(Message):
        pass

    class Leave(Message):
        pass

    def action_confirm(self) -> None:
        self.post_message(self.Confirm())

    def action_down(self) -> None:
        if self.highlighted is None or self.highlighted >= self.option_count - 1:
            self.post_message(self.Leave())
        else:
            self.action_cursor_down()


class SelectAction(Action[Select]):
    """The options as a list with tick boxes, and the Other field under it. Enter, in the list
    or in the field, answers with everything ticked, plus the field's text when it has some."""

    def compose(self) -> ComposeResult:
        question = self.question
        yield Picker(
            *((option, option, option in question.selected) for option in question.options),
            id="action-picker",
        )
        if question.other:
            yield OtherField(question.other, multiple=True)

    def focus_first(self) -> None:
        self.query_one(Picker).focus()

    def on_picker_leave(self, event: Picker.Leave) -> None:
        event.stop()
        for field in self.query(OtherField):
            field.input.focus()

    def on_other_input_leave(self, event: OtherInput.Leave) -> None:
        event.stop()
        if event.direction < 0:
            self.focus_first()

    def on_picker_confirm(self, event: Picker.Confirm) -> None:
        event.stop()
        self.commit(self.current())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.commit(self.current())

    def current(self) -> Answer:
        """Everything ticked, in the order of the options, and the Other text if there is some."""
        ticked = set(self.query_one(Picker).selected)
        values = [option for option in self.question.options if option in ticked]
        other = ""
        for field in self.query(OtherField):
            other = field.text
        if other:
            values.append(other)
        return Answer(values=tuple(values), other=bool(other))
