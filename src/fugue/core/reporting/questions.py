"""Where a flow stops and asks the person running it, and what comes back.

There are three kinds of question. Each is a small model; an interface decides how to show it
(buttons, a list, a text box) and answers with an `Answer`.

- `Ask`: pick one of some options.
- `Select`: pick any number of options.
- `Chat`: write a free reply.
- `Form`: a group of the questions above, answered together and submitted at once.

`Ask` and `Select` can offer an Other option: a text field the person writes in, which counts
as a chosen answer once it has text.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict

PERMISSION = "permission"
"""The tag of a question that only asks leave to go on. A run started with `auto` answers it
by itself, with the question's default answer, so such a question must default to yes."""

HOLD = "hold"
"""The tag of a question the run waits on quietly: a screen does not open it by itself, and the
person calls it up when ready (the pause between steps is one)."""


class Answer(BaseModel):
    """What the person answered. `values` holds the chosen options, or the written text;
    `other` says the last value was typed into the Other field, not picked from the options.
    The answer to a `Form` has one answer per field in `answers`, in the form's order."""

    model_config = ConfigDict(frozen=True)

    values: tuple[str, ...] = ()
    other: bool = False
    answers: tuple["Answer", ...] = ()

    @property
    def value(self) -> str:
        """The first (for `Ask` and `Chat`, the only) value."""
        return self.values[0] if self.values else ""

    @property
    def text(self) -> str:
        """All values on one line; for a form, each field's answer, separated by a bar."""
        if self.answers:
            return " | ".join(answer.text for answer in self.answers)
        return ", ".join(self.values)


class Question(BaseModel, ABC):
    """What every question has: the `text` that asks it, a `body` in Markdown to read before
    answering (a plan, a summary), `tags` that say what kind of question it is, and `asked_by`,
    the name of the agent that asks when one does, so the screen can say who is asking.

    A question tagged `PERMISSION` only asks leave to go on, and an automatic run skips it. One
    tagged `HOLD` waits until the person calls it up."""

    model_config = ConfigDict(frozen=True)

    text: str
    body: str = ""
    tags: tuple[str, ...] = ()
    asked_by: str = ""

    @property
    def is_permission(self) -> bool:
        return PERMISSION in self.tags

    @property
    def is_hold(self) -> bool:
        return HOLD in self.tags

    @abstractmethod
    def default_answer(self) -> Answer:
        """The answer for a run nobody is watching."""


class Ask(Question):
    """Pick one option. `default` is preselected. `other`, when set, adds a text field with
    this label as one more option."""

    options: tuple[str, ...] = ("yes", "no")
    default: str = "no"
    other: str | None = None

    def default_answer(self) -> Answer:
        return Answer(values=(self.default,))


class Select(Question):
    """Pick any number of options; `selected` start ticked. `other`, when set, adds a text field
    with this label that is ticked once it has text."""

    options: tuple[str, ...]
    selected: tuple[str, ...] = ()
    other: str | None = None

    def default_answer(self) -> Answer:
        return Answer(values=self.selected)


class Chat(Question):
    """Write a reply. `placeholder` hints at what to write; `default` is the text if nothing is
    written."""

    placeholder: str = ""
    default: str = ""

    def default_answer(self) -> Answer:
        return Answer(values=(self.default,))


class Form(Question):
    """A group of questions shown together. The person moves between them, answers each, and
    submits all at once; the answer has one entry per field, in order. `text` titles the form."""

    fields: tuple[Ask | Select | Chat, ...]
    submit: str = "Submit"

    def default_answer(self) -> Answer:
        return Answer(answers=tuple(field.default_answer() for field in self.fields))
