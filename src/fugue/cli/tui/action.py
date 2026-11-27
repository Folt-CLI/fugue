"""The window at the bottom where the flow asks the person running it for something."""

import asyncio
from typing import ClassVar

from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import ItemGrid, Vertical, VerticalScroll
from textual.events import Key
from textual.widget import Widget
from textual.widgets import Button, Label, Static

from fugue.core.reporting import Question


class ActionWindow(Vertical):
    """Hidden until the flow asks something, then shown under the other windows with the
    question's text, its body and a button for each choice. A key that starts a choice picks it.

    It is made of three parts that are set from the question and nothing else:
    `#action-text` (the question), `#action-body` (what to read first, as Markdown, hidden when
    empty) and `#action-choices` (the answers, in a grid that wraps when they do not fit). A
    new kind of question changes those parts in `_present`; nothing else in the screen knows
    how a question looks.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left", "choose(-1)", "Choose", key_display="←→"),
        Binding("right", "choose(1)", "Choose", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(id="actions")
        self.border_title = "Action needed"
        self.display = False
        self._question: Question | None = None
        self._answer: asyncio.Future[str] | None = None

    def compose(self) -> ComposeResult:
        yield Label(id="action-text")
        with VerticalScroll(id="action-body"):
            yield Static()
        yield ItemGrid(id="action-choices")

    @property
    def waiting(self) -> bool:
        """Whether a question is open."""
        return self._answer is not None

    async def ask(self, question: Question, *, returning_to: Widget | None = None) -> str:
        """Show the question and wait for an answer. Focus goes back to `returning_to` after."""
        self._question = question
        self._answer = asyncio.get_running_loop().create_future()
        await self._present(question)
        self.display = True
        self.focus_default()
        try:
            return await self._answer
        finally:
            self.display = False
            self._answer = None
            self._question = None
            if returning_to is not None and returning_to.is_attached:
                returning_to.focus()

    def focus_default(self) -> None:
        """Put focus on the preselected answer."""
        for button in self.query(Button):
            if self._question is not None and button.name == self._question.default:
                button.focus()

    async def _present(self, question: Question) -> None:
        """Fill the three parts from the question."""
        self.query_one("#action-text", Label).update(question.text)
        body = self.query_one("#action-body", VerticalScroll)
        body.display = bool(question.body)
        self.query_one("#action-body Static", Static).update(Markdown(question.body))
        choices = self.query_one("#action-choices", ItemGrid)
        widest = max(len(choice) for choice in question.choices) + 8
        choices.min_column_width = max(widest, 14)
        choices.max_column_width = max(widest, 14) + 10
        await choices.remove_children()
        await choices.mount(
            *(
                Button(self._label(choice, question.choices), name=choice)
                for choice in question.choices
            )
        )

    @staticmethod
    def _label(choice: str, choices: tuple[str, ...]) -> Text:
        """A choice with the letter that picks it underlined, if no choice before it has it."""
        first = next(c for c in choices if c[:1].lower() == choice[:1].lower())
        if first != choice:
            return Text(choice)
        return Text.assemble((choice[:1], "bold underline"), choice[1:])

    def action_choose(self, step: int) -> None:
        """Move to the previous or next answer, wrapping around."""
        buttons = list(self.query(Button))
        if not buttons:
            return
        focused = self.app.focused
        index = buttons.index(focused) if focused in buttons else 0
        buttons[(index + step) % len(buttons)].focus()

    def _choose(self, choice: str) -> None:
        if self._answer is not None and not self._answer.done():
            self._answer.set_result(choice)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if self._question is not None:
            self._choose(event.button.name or self._question.default)

    def on_key(self, event: Key) -> None:
        if self._question is None or not event.character:
            return
        for choice in self._question.choices:
            if choice.lower().startswith(event.character.lower()):
                event.stop()
                event.prevent_default()
                self._choose(choice)
                return
