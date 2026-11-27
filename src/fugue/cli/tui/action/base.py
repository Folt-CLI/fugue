"""The base every action shares."""

import asyncio
from abc import abstractmethod

from textual.containers import Vertical
from textual.message import Message

from fugue.core.reporting import Answer, Question


class Action[QuestionT: Question](Vertical):
    """The part of the question window that takes the answer: buttons, a list, a text box.

    An action is made for one question, fills itself from it, and ends with `commit`. The
    window around it (the question's text and body) is the same for every action. To add a kind
    of question: write its model in core, write an action for it here, and add the pair to
    `ActionWindow.ACTIONS`.

    An action can also be embedded in a form. It then does not end the answer when it commits;
    it remembers it, tells the form (`Committed`) so the form can move on, and the form reads
    `current` from every field when it is submitted.
    """

    class Committed(Message):
        """An embedded action has an answer."""

    def __init__(self, question: QuestionT, *, embedded: bool = False) -> None:
        super().__init__()
        self.question = question
        self.embedded = embedded
        self.chosen: Answer | None = None
        self._answer: asyncio.Future[Answer] = asyncio.get_running_loop().create_future()

    @abstractmethod
    def focus_first(self) -> None:
        """Give focus to the part the person answers with first."""

    def current(self) -> Answer:
        """The answer as things stand: what was chosen, or else the question's default."""
        return self.chosen or self.question.default_answer()

    def commit(self, answer: Answer) -> None:
        """The person has answered. Ends the question, or, in a form, moves the form on."""
        self.chosen = answer
        if self.embedded:
            self.post_message(self.Committed())
        else:
            self.finish(answer)

    async def answer(self) -> Answer:
        """Wait for the answer."""
        return await self._answer

    def finish(self, answer: Answer) -> None:
        if not self._answer.done():
            self._answer.set_result(answer)
