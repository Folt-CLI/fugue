"""The screen `fugue work` runs in."""

from pathlib import Path
from typing import ClassVar

from rich.console import RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Static

from fugue.cli.tui.question import QuestionScreen
from fugue.cli.tui.reporter import TuiReporter
from fugue.cli.tui.steps import StepList
from fugue.cli.views import TicketView, WorkView
from fugue.core.reporting import (
    Event,
    Message,
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


class WorkApp(App[None]):
    """Runs the work flow for one ticket and shows it live: the steps on the left, what they
    found on the right. The screen stays open when the run ends, so the result can be read;
    `q` closes it. After `run()` returns, `result` or `error` says how it went."""

    CSS_PATH: ClassVar[Path] = Path(__file__).with_name("app.tcss")
    BINDINGS: ClassVar[list[BindingType]] = [Binding("q", "quit", "Quit")]

    def __init__(self, work: Work, number: int) -> None:
        super().__init__()
        self._work = work
        self._number = number
        self._tickets = TicketView()
        self.result: WorkResult | None = None
        self.error: FugueError | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield StepList(id="steps")
            yield VerticalScroll(id="feed")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"fugue work #{self._number}"
        self.sub_title = "running"
        self.run_worker(self._run(), exit_on_error=False)

    async def _run(self) -> None:
        try:
            self.result = await self._work.run(self._number, TuiReporter(self))
        except FugueError as error:
            await self._fail(error)
        except Exception as error:  # a worker would swallow this and leave the screen running
            await self._fail(FlowError(f"{type(error).__name__}: {error}"))

    async def _fail(self, error: FugueError) -> None:
        self.error = error
        self.sub_title = "failed, press q to close"
        await self._add(Panel(Text(str(error)), title="[bold red]failed[/]", border_style="red"))

    async def show(self, event: Event) -> None:
        """Show one event from the flow."""
        steps = self.query_one(StepList)
        match event:
            case StepStarted(step=step):
                steps.start(step)
            case StepFinished(step=step, detail=detail):
                steps.finish(step, detail)
            case StepFailed(step=step):
                steps.fail(step)
            case TicketRead(repository=repository, ticket=ticket):
                await self._add(self._tickets.panel(ticket, repository))
            case WorktreeReady() as ready:
                first, second = WorkView.worktree_lines(
                    ready.path,
                    ready.branch,
                    ready.base,
                    new_branch=ready.new_branch,
                    reused=ready.reused,
                )
                await self._add(Text.from_markup(f"{first}\n{second}"))
            case Message(text=text, level=level):
                await self._add(Text(text, style="yellow" if level == "warning" else "dim"))
            case RunFinished():
                self.sub_title = "done, press q to close"
            case RunFailed():
                self.sub_title = "failed, press q to close"

    async def ask(self, question: Question) -> str:
        """Stop the flow until the person has chosen an answer."""
        answer: str = await self.push_screen_wait(QuestionScreen(question))
        await self._add(Text.from_markup(f"[dim]{question.text}[/] [bold]{answer}[/]"))
        return answer

    async def _add(self, content: RenderableType) -> None:
        feed = self.query_one("#feed", VerticalScroll)
        await feed.mount(Static(content))
        feed.scroll_end(animate=False)
