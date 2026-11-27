"""The Other option: a text field that is an answer once it has text."""

from typing import ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Input, Static


class OtherInput(Input):
    """A text box that tells its action when the arrow keys leave it."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "leave(-1)", "Back to the options", key_display="↑", show=False),
        Binding("down", "leave(1)", "Down", show=False),
    ]

    class Leave(Message):
        def __init__(self, direction: int) -> None:
            super().__init__()
            self.direction = direction

    def action_leave(self, direction: int) -> None:
        self.post_message(self.Leave(direction))


class OtherField(Horizontal):
    """A line with a marker and a text box. The marker fills in as soon as there is text or the
    box has focus, which is what makes the field a selected option. `multiple` draws it as a
    tick box, for a list where several options can be chosen."""

    def __init__(self, label: str, *, multiple: bool = False) -> None:
        super().__init__(id="action-other")
        self._label = label
        self._multiple = multiple
        self._marker = Static(classes="marker")
        self.input = OtherInput(placeholder=f"{label}...")

    def compose(self) -> ComposeResult:
        yield self._marker
        yield self.input

    def on_mount(self) -> None:
        self._draw()

    @property
    def text(self) -> str:
        return self.input.value.strip()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._draw()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self._draw()

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        self._draw()

    def _draw(self) -> None:
        chosen = bool(self.text) or self.input.has_focus
        on, off = ("☑", "☐") if self._multiple else ("●", "○")
        self._marker.update(Text(on if chosen else off, style="bold" if chosen else "dim"))
        self.set_class(chosen, "-chosen")
