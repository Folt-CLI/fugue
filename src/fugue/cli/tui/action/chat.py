"""Write a free reply."""

from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widgets import Static, TextArea

from fugue.cli.tui.action.base import Action
from fugue.core.reporting import Answer, Chat


class Composer(TextArea):
    """A text box where Enter sends and Ctrl+J starts a new line."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+j", "newline", "New line"),
    ]

    class Send(Message):
        pass

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Send())
        else:
            await super()._on_key(event)

    def action_newline(self) -> None:
        self.insert("\n")


class ChatAction(Action[Chat]):
    """A text box for a free reply, with a line saying how to send it."""

    def compose(self) -> ComposeResult:
        yield Composer(
            self.question.default, placeholder=self.question.placeholder, id="action-chat"
        )
        yield Static("Enter sends · Ctrl+J adds a line", classes="hint")

    def focus_first(self) -> None:
        self.query_one(Composer).focus()

    def current(self) -> Answer:
        text = self.query_one(Composer).text.strip()
        return Answer(values=(text or self.question.default,))

    def on_composer_send(self, event: Composer.Send) -> None:
        event.stop()
        answer = self.current()
        if answer.value or self.embedded:  # in a form, a field may be left empty
            self.commit(answer)
