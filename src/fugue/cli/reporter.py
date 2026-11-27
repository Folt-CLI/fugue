"""Shows what a core flow reports, with Rich."""

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.status import Status

from fugue.cli.views import TicketView, WorkView
from fugue.core.reporting import (
    AgentFinished,
    AgentStarted,
    Answer,
    Ask,
    Chat,
    Event,
    Form,
    Message,
    Output,
    Question,
    Reporter,
    Select,
    StatusMoved,
    StepFailed,
    StepFinished,
    StepStarted,
    TicketDetails,
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
            case TicketDetails(fields=fields):
                self._stop()
                console.print(self._tickets.fields_panel(fields))
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
            case AgentStarted(agent=agent, role=role):
                self._stop()
                console.print(f"[cyan]▶[/] {agent}" + (f" [dim]· {role}[/]" if role else ""))
            case AgentFinished(agent=agent, failed=failed):
                self._stop()
                console.print(f"[bold red]✗[/] {agent}" if failed else f"[bold green]✓[/] {agent}")
            case Output(stream="ai", text=text, agent=agent):
                self._stop()
                prefix = f"[{agent}] " if agent else ""
                console.print(f"{prefix}{text}", markup=False, highlight=False)
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

    async def ask(self, question: Question) -> Answer:
        """Ask on the terminal. Giving up (Ctrl-C, or no input left) stops the flow."""
        self._stop()
        if question.body:
            self._console.print(Markdown(question.body))
        try:
            match question:
                case Ask():
                    return self._pick_one(question)
                case Select():
                    return self._pick_many(question)
                case Chat():
                    return self._write(question)
                case Form():
                    return await self._fill(question)
                case _:
                    return question.default_answer()
        except typer.Abort as abort:
            raise FlowStopped("Stopped: no answer was given.") from abort

    async def _fill(self, form: Form) -> Answer:
        """Ask each question of the form in turn; the form is submitted by the last answer."""
        self._console.print(f"[bold]{form.text}[/]")
        answers = []
        for number, field in enumerate(form.fields, 1):
            self._console.print(f"[dim]Question {number} of {len(form.fields)}[/]")
            answers.append(await self.ask(field))
        return Answer(answers=tuple(answers))

    def _pick_one(self, question: Ask) -> Answer:
        """Type an option, or the first letters of it; `other` asks for the text next."""
        other = question.other.lower() if question.other else None
        names = [*question.options, *([other] if other else [])]
        listed = "/".join(names)
        while True:
            typed = str(
                typer.prompt(f"{question.text} [{listed}]", default=question.default)
            ).strip()
            matches = [name for name in names if typed and name.lower().startswith(typed.lower())]
            if len(matches) != 1:
                self._console.print(f"[red]Please answer one of: {listed}[/]")
            elif matches[0] == other:
                return Answer(values=(self._text(question.other or "Other"),), other=True)
            else:
                return Answer(values=(matches[0],))

    def _pick_many(self, question: Select) -> Answer:
        """Type numbers or names, separated by commas; `other` is the next number."""
        names = list(question.options)
        if question.other:
            names.append(question.other)
        for number, name in enumerate(names, 1):
            tick = "x" if name in question.selected else " "
            self._console.print(f"  [{tick}] {number}. {name}", markup=False)
        preset = ",".join(str(names.index(s) + 1) for s in question.selected if s in names)
        while True:
            typed = str(
                typer.prompt("Choose, separated by commas, or none", default=preset)
            ).strip()
            chosen: list[str] = []
            for part in filter(None, (p.strip() for p in typed.split(","))):
                found = (
                    names[int(part) - 1]
                    if part.isdigit() and 0 < int(part) <= len(names)
                    else next((n for n in names if n.lower().startswith(part.lower())), None)
                )
                if found is None:
                    break
                if found not in chosen:
                    chosen.append(found)
            else:
                values = [name for name in chosen if name != question.other]
                if question.other and question.other in chosen:
                    values.append(self._text(question.other))
                return Answer(values=tuple(values), other=bool(question.other in chosen))
            self._console.print("[red]Use the numbers or names above.[/]")

    def _write(self, question: Chat) -> Answer:
        """A reply, on one line. Empty is not accepted unless there is a default."""
        while True:
            text = str(typer.prompt(question.text, default=question.default)).strip()
            if text:
                return Answer(values=(text,))
            self._console.print("[red]Please write something.[/]")

    def _text(self, label: str) -> str:
        """The text for an Other option."""
        while True:
            text = str(typer.prompt(label, default="")).strip()
            if text:
                return text

    def _stop(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None
