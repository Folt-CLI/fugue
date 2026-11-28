"""The window at the bottom where the flow asks the person running it for something."""

from typing import ClassVar

from rich.markdown import Markdown
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Label, Static

from fugue.cli.tui.action.ask import AskAction
from fugue.cli.tui.action.base import Action
from fugue.cli.tui.action.chat import ChatAction
from fugue.cli.tui.action.select import SelectAction
from fugue.core.reporting import Answer, Ask, Chat, Question, Select


class ActionWindow(Vertical):
    """Hidden until the flow asks something, then shown under the other windows.

    It shows the question's text and body, which every kind of question has, and under them the
    action that matches the kind of question (`ACTIONS`): buttons, a tick list, a text box.
    A form is too big for this window and has a screen of its own (`FormScreen`).
    """

    ACTIONS: ClassVar[dict[type[Question], type[Action]]] = {  # type: ignore[type-arg]
        Ask: AskAction,
        Select: SelectAction,
        Chat: ChatAction,
    }

    def __init__(self) -> None:
        super().__init__(id="actions")
        self.border_title = "Action needed"
        self.display = False
        self._action: Action | None = None  # type: ignore[type-arg]

    def compose(self) -> ComposeResult:
        yield Label(id="action-text")
        with VerticalScroll(id="action-body"):
            yield Static()
        yield Vertical(id="action-area")

    @property
    def waiting(self) -> bool:
        """Whether a question is open."""
        return self._action is not None

    @property
    def is_open(self) -> bool:
        return bool(self.display)

    async def ask(self, question: Question, *, returning_to: Widget | None = None) -> Answer:
        """Show the question and wait for an answer. Focus goes back to `returning_to` after.

        A question tagged as a hold is not shown: it waits until `show` is called."""
        self.border_title = f"{question.asked_by} asks" if question.asked_by else "Action needed"
        action = self.ACTIONS[type(question)](question)
        await self._present(question, action)
        self._action = action
        if question.is_hold:
            self.display = False
        else:
            self.show()
        try:
            return await action.answer()
        finally:
            self.display = False
            self._action = None
            if returning_to is not None and returning_to.is_attached:
                returning_to.focus()

    def show(self) -> None:
        """Open the pending question and focus it."""
        if self._action is not None:
            self.display = True
            self.call_after_refresh(self._action.focus_first)

    def hide(self) -> None:
        """Put the pending question away. It stays pending until it is shown and answered."""
        self.display = False

    def focus_first(self) -> None:
        """Bring focus back to the open question."""
        if self._action is not None:
            self._action.focus_first()

    async def _present(self, question: Question, action: Action) -> None:  # type: ignore[type-arg]
        """Fill the parts from the question and mount its action."""
        self.query_one("#action-text", Label).update(question.text)
        body = self.query_one("#action-body", VerticalScroll)
        body.display = bool(question.body)
        self.query_one("#action-body Static", Static).update(Markdown(question.body))
        area = self.query_one("#action-area", Vertical)
        await area.remove_children()
        await area.mount(action)
