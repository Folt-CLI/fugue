"""What happens around every run: events say which run they belong to, and are recorded."""

from pathlib import Path

from fugue.core.reporting import Answer, Answered, Event, Message, Question, Reporter


class RunReporter(Reporter):
    """Wraps the reporter of whoever starts a flow.

    Every event is stamped with the run id and the flow, appended to the run's record when
    there is one, and then passed on. A question is passed on too, and its answer is recorded;
    with `auto`, a question tagged as a permission is answered with its default instead.
    The steps of a flow only ever see this reporter, so they never handle ids or files.
    """

    def __init__(
        self, inner: Reporter, *, run_id: str, flow: str, record: Path | None, auto: bool = False
    ) -> None:
        self._inner = inner
        self._auto = auto
        self._run_id = run_id
        self._flow = flow
        self._record = record

    @property
    def can_hold(self) -> bool:  # type: ignore[override]
        return self._inner.can_hold

    @property
    def record(self) -> Path | None:
        """The file this run is recorded in, if it is recorded."""
        return self._record

    async def report(self, event: Event) -> None:
        stamped = event.model_copy(update={"run_id": self._run_id, "flow": self._flow})
        self._write(stamped)
        await self._inner.report(stamped)

    async def ask(self, question: Question) -> Answer:
        auto = self._auto and question.is_permission
        if auto:
            answer = question.default_answer()
            await self.report(Message(text=f"{question.text} {answer.text} (automatic)"))
        else:
            answer = await self._inner.ask(question)
        self._write(
            Answered(
                run_id=self._run_id,
                flow=self._flow,
                question=question.text,
                answer=answer.text,
                other=answer.other,
                auto=auto,
            )
        )
        return answer

    def _write(self, event: Event) -> None:
        if self._record is None:
            return
        self._record.parent.mkdir(parents=True, exist_ok=True)
        with self._record.open("a", encoding="utf-8") as file:
            file.write(event.model_dump_json() + "\n")
