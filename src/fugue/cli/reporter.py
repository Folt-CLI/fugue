"""Shows what a core flow reports, with Rich."""

import typer
from rich.status import Status

from fugue.cli.output import Output
from fugue.core.reporting import (
    Event,
    Message,
    Question,
    Reporter,
    StatusMoved,
    StepFailed,
    StepFinished,
    StepStarted,
    TicketRead,
)


class RichReporter(Reporter):
    """Prints the steps of a flow as they happen. With `quiet`, it shows no events at all;
    questions are still asked, because the flow cannot go on without an answer."""

    def __init__(self, output: Output, *, quiet: bool = False) -> None:
        self._output = output
        self._quiet = quiet
        self._spinner: Status | None = None

    async def report(self, event: Event) -> None:
        if self._quiet:
            return
        console = self._output.console
        match event:
            case StepStarted(step=step):
                self._stop()
                self._spinner = console.status(f"[bold]{step}[/]", spinner="dots")
                self._spinner.start()
            case StepFinished(step=step, detail=detail):
                self._stop()
                note = f" [dim]· {detail}[/]" if detail else ""
                console.print(f"[bold green]✓[/] {step}{note}")
            case StepFailed(step=step):
                self._stop()
                console.print(f"[bold red]✗[/] {step}")
            case TicketRead(repository=repository, ticket=ticket):
                self._stop()
                self._output.ticket(ticket, repository)
            case StatusMoved():
                pass
            case Message(text=text, level=level):
                self._stop()
                style = "yellow" if level == "warning" else "dim"
                console.print(f"[{style}]{text}[/]")

    async def ask(self, question: Question) -> str:
        self._stop()
        options = "/".join(question.choices)
        while True:
            answer = str(typer.prompt(f"{question.text} [{options}]", default=question.default))
            if answer in question.choices:
                return answer
            self._output.console.print(f"[red]Please answer one of: {options}[/]")

    def _stop(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
