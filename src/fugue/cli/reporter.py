"""Shows what a core flow reports, with Rich."""

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.status import Status

from fugue.cli.views import TicketView, WorkView
from fugue.core.reporting import (
    Event,
    Message,
    Output,
    Question,
    Reporter,
    StatusMoved,
    StepFailed,
    StepFinished,
    StepStarted,
    TicketRead,
    WorktreeReady,
)
from fugue.errors import FlowStopped


class RichReporter(Reporter):
    """Prints the steps of a flow as they happen. With `quiet`, it shows no events at all;
    questions are still asked, because the flow cannot go on without an answer."""

    def __init__(self, console: Console | None = None, *, quiet: bool = False) -> None:
        self._console = console or Console()
        self._errors = Console(stderr=True)
        self._tickets = TicketView(self._console)
        self._work = WorkView(self._console)
        self._quiet = quiet
        self._spinner: Status | None = None

    async def report(self, event: Event) -> None:
        if self._quiet:
            return
        console = self._console
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
                self._tickets.show(ticket, repository)
            case StatusMoved():
                pass
            case WorktreeReady() as ready:
                self._stop()
                self._work.worktree(
                    ready.path,
                    ready.branch,
                    ready.base,
                    new_branch=ready.new_branch,
                    reused=ready.reused,
                )
            case Output(stream=stream, text=text):
                self._stop()
                if stream == "stderr":
                    self._errors.print(text, style="red", markup=False, highlight=False)
                else:
                    style = "dim" if stream == "stdout" else None
                    console.print(text, style=style, markup=False, highlight=False)
            case Message(text=text, level=level):
                self._stop()
                style = "yellow" if level == "warning" else "dim"
                console.print(f"[{style}]{text}[/]")

    async def ask(self, question: Question) -> str:
        """Ask on the terminal. The first letters of a choice are enough. Giving up (Ctrl-C,
        or no input left) stops the flow."""
        self._stop()
        if question.body:
            self._console.print(Markdown(question.body))
        options = "/".join(question.choices)
        while True:
            try:
                typed = str(typer.prompt(f"{question.text} [{options}]", default=question.default))
            except typer.Abort as abort:
                raise FlowStopped("Stopped: no answer was given.") from abort
            matches = [c for c in question.choices if c.startswith(typed.strip().lower())]
            if typed.strip() and len(matches) == 1:
                return matches[0]
            self._console.print(f"[red]Please answer one of: {options}[/]")

    def _stop(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
