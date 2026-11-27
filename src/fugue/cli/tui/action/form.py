"""A group of questions answered together."""

from typing import ClassVar

from rich.markdown import Markdown
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, Label, Static

from fugue.cli.tui.action.ask import AskAction
from fugue.cli.tui.action.base import Action
from fugue.cli.tui.action.chat import ChatAction
from fugue.cli.tui.action.select import SelectAction
from fugue.core.reporting import Answer, Ask, Chat, Form, Question, Select


class FormAction(Action[Form]):
    """The form's questions one under another in a scrolling area, each with its own action,
    and a separate Submit button under them, which is how the form is finished. The button
    stays in view while the questions scroll.

    Tab and Shift+Tab move between the questions, and answering one moves on to the next. Ctrl+S
    or the Submit button sends every answer at once. A field that was not answered gives its
    default."""

    FIELDS: ClassVar[dict[type[Question], type[Action]]] = {  # type: ignore[type-arg]
        Ask: AskAction,
        Select: SelectAction,
        Chat: ChatAction,
    }

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+s", "submit", "Submit"),
        Binding("tab", "app.focus_next", "Next question", key_display="Tab"),
    ]

    def __init__(self, question: Form) -> None:
        super().__init__(question)
        self._actions: list[Action] = []  # type: ignore[type-arg]
        self._progress = Static(id="form-progress")

    def compose(self) -> ComposeResult:
        yield self._progress
        with VerticalScroll(id="form-fields"):
            for index, field in enumerate(self.question.fields, 1):
                action = self.FIELDS[type(field)](field, embedded=True)
                self._actions.append(action)
                with Vertical(classes="form-field", id=f"form-field-{index}"):
                    yield Label(field.text, classes="form-question")
                    if field.body:
                        yield Static(Markdown(field.body), classes="form-body")
                    yield action
        with Horizontal(id="form-actions"):
            yield Button(self.question.submit, id="form-submit")

    def on_mount(self) -> None:
        self._show_progress(0)

    def focus_first(self) -> None:
        if self._actions:
            self._actions[0].focus_first()
        else:
            self.query_one("#form-submit", Button).focus()

    def current(self) -> Answer:
        return Answer(answers=tuple(action.current() for action in self._actions))

    def action_submit(self) -> None:
        self.finish(self.current())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "form-submit":
            event.stop()
            self.action_submit()

    def on_action_committed(self, event: Action.Committed) -> None:
        """A field was answered: go on to the next one, or to Submit after the last."""
        event.stop()
        index = self._index_of(self.app.focused)
        if index is not None and index + 1 < len(self._actions):
            self._actions[index + 1].focus_first()
        else:
            self.query_one("#form-submit", Button).focus()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        index = self._index_of(event.widget)
        if index is not None:
            self._show_progress(index)

    def _index_of(self, widget: Widget | None) -> int | None:
        """The position of the field that holds this widget."""
        if widget is None:
            return None
        for index, action in enumerate(self._actions):
            if widget is action or action in widget.ancestors:
                return index
        return None

    def _show_progress(self, index: int) -> None:
        total = len(self._actions)
        self._progress.update(f"Question {index + 1} of {total}" if total else "")
