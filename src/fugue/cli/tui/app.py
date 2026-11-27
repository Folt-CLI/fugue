"""The screen `fugue work` runs in."""

from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header

from fugue.cli.tui.question import QuestionScreen
from fugue.cli.tui.reporter import TuiReporter
from fugue.cli.tui.status import StatusBar
from fugue.cli.tui.windows import (
    AiWindow,
    ChecklistWindow,
    StderrWindow,
    StdoutWindow,
    TicketWindow,
    Window,
)
from fugue.cli.views import WorkView
from fugue.core.reporting import (
    Event,
    Message,
    Output,
    Question,
    RunFailed,
    RunFinished,
    StepFailed,
    StepFinished,
    StepStarted,
    TicketRead,
    WorktreeReady,
)
from fugue.core.work import Work, WorkResult
from fugue.errors import FlowError, FugueError

WINDOWS: tuple[type[Window], ...] = (
    ChecklistWindow,
    TicketWindow,
    StdoutWindow,
    StderrWindow,
    AiWindow,
)


class WorkApp(App[None]):
    """Runs the work flow for one ticket and shows it live: the checklist of every step, the
    ticket, and three log windows (STDOUT, STDERR, AI), with a status bar under them that
    names the current step.
    A window is shown or hidden with its key; Tab moves between them. A question from the flow
    floats over whatever is showing.

    The screen stays open when the run ends, so the result can be read; `q` closes it. After
    `run()` returns, `result` or `error` says how it went."""

    CSS_PATH: ClassVar[Path] = Path(__file__).with_name("app.tcss")
    BINDINGS: ClassVar[list[BindingType]] = [
        *(Binding(window.key, f"toggle('{window.key}')", window.title) for window in WINDOWS),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, work: Work, number: int) -> None:
        super().__init__()
        self._work = work
        self._number = number
        self.result: WorkResult | None = None
        self.error: FugueError | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="windows"):
            yield ChecklistWindow(self._work.steps())
            with Vertical(id="main"):
                yield TicketWindow()
                with Horizontal(id="logs"):
                    yield StdoutWindow()
                    yield StderrWindow()
                    yield AiWindow()
        yield StatusBar()
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"fugue work #{self._number}"
        self.run_worker(self._run(), exit_on_error=False)

    async def action_toggle(self, key: str) -> None:
        """Show or hide the window that has this key."""
        for window in self.query(Window):
            if window.key == key:
                window.toggle()

    async def _run(self) -> None:
        try:
            self.result = await self._work.run(self._number, TuiReporter(self))
        except FugueError as error:
            await self._fail(error)
        except Exception as error:  # a worker would swallow this and leave the screen running
            await self._fail(FlowError(f"{type(error).__name__}: {error}"))

    async def _fail(self, error: FugueError) -> None:
        self.error = error
        self.query_one(StatusBar).failed(str(error))
        await self.query_one(StderrWindow).write(str(error))

    async def show(self, event: Event) -> None:
        """Show one event from the flow in the window it belongs to."""
        checklist = self.query_one(ChecklistWindow)
        status = self.query_one(StatusBar)
        match event:
            case StepStarted(step=step):
                checklist.start(step)
                status.running(step, checklist.position(step), checklist.total)
            case StepFinished(step=step, detail=detail):
                checklist.finish(step, detail)
            case StepFailed(step=step):
                checklist.fail(step)
            case TicketRead(repository=repository, ticket=ticket):
                await self.query_one(TicketWindow).show(ticket, repository)
            case WorktreeReady() as ready:
                first, second = WorkView.worktree_lines(
                    ready.path,
                    ready.branch,
                    ready.base,
                    new_branch=ready.new_branch,
                    reused=ready.reused,
                )
                await self.query_one(StdoutWindow).write(Text.from_markup(f"{first}\n{second}"))
            case Message(text=text, level="warning"):
                await self.query_one(StderrWindow).write(text)
            case Message(text=text):
                await self.query_one(StdoutWindow).write(text)
            case Output(stream="stdout", text=text):
                await self.query_one(StdoutWindow).write(text)
            case Output(stream="stderr", text=text):
                await self.query_one(StderrWindow).write(text)
            case Output(stream="ai", text=text):
                await self.query_one(AiWindow).write(text)
            case RunFinished():
                status.finished()
            case RunFailed():
                pass  # the error is shown once, when `_run` catches it

    async def ask(self, question: Question) -> str:
        """Stop the flow until the person has chosen an answer."""
        answer: str = await self.push_screen_wait(QuestionScreen(question))
        await self.query_one(StdoutWindow).write(
            Text.from_markup(f"[dim]{question.text}[/] [bold]{answer}[/]")
        )
        return answer
