"""The screen `fugue work` runs in."""

from pathlib import Path
from typing import ClassVar

from rich.console import RenderableType
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.widgets import Footer, Header, TabbedContent, TabPane

from fugue.cli.tui.question import QuestionScreen
from fugue.cli.tui.reporter import TuiReporter
from fugue.cli.tui.status import StatusBar
from fugue.cli.tui.windows import (
    AiWindow,
    ChecklistWindow,
    LogWindow,
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

PAGES: tuple[type[Window], ...] = (TicketWindow, StdoutWindow, StderrWindow, AiWindow)


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
        Binding(ChecklistWindow.key, "toggle_checklist", ChecklistWindow.title),
        *(Binding(page.key, f"show_page('{page.key}')", page.title) for page in PAGES),
        Binding("H", "move('left')", "Move", key_display="HJKL"),
        Binding("J", "move('down')", "Next page", show=False),
        Binding("K", "move('up')", "Previous page", show=False),
        Binding("L", "move('right')", "Page", show=False),
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
            with TabbedContent(id="pages", initial=self._pane(TicketWindow)):
                for page in PAGES:
                    with TabPane(self._label(page), id=self._pane(page)):
                        yield page()
        yield StatusBar()
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"fugue work #{self._number}"
        self.run_worker(self._run(), exit_on_error=False)

    async def action_toggle_checklist(self) -> None:
        self.query_one(ChecklistWindow).toggle()

    async def action_show_page(self, key: str) -> None:
        """Bring the page with this key to the front and focus it, so the arrow keys scroll it."""
        self.query_one(TabbedContent).active = f"page-{key}"
        for page in PAGES:
            if page.key == key:
                self.call_after_refresh(self.query_one(page).focus)

    async def action_move(self, direction: str) -> None:
        """Move the selection with Shift+H J K L: H to the checklist, L to the page in front,
        J and K to the next and previous page."""
        tabs = self.query_one(TabbedContent)
        keys = [page.key for page in PAGES]
        current = keys.index(tabs.active.removeprefix("page-"))
        match direction:
            case "left":
                checklist = self.query_one(ChecklistWindow)
                if checklist.display:
                    checklist.focus()
            case "right":
                await self.action_show_page(keys[current])
            case "down":
                await self.action_show_page(keys[(current + 1) % len(keys)])
            case "up":
                await self.action_show_page(keys[(current - 1) % len(keys)])

    @staticmethod
    def _pane(page: type[Window]) -> str:
        return f"page-{page.key}"

    @staticmethod
    def _label(page: type[Window], *, unseen: bool = False) -> str:
        return f"{page.title} ({page.key}){' •' if unseen else ''}"

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """A page that comes to the front has been seen."""
        for page in PAGES:
            if self._pane(page) == event.pane.id:
                self.query_one(TabbedContent).get_tab(event.pane.id).label = self._label(page)

    async def _write(self, page: type[LogWindow], content: RenderableType) -> None:
        """Write to a page, and mark it if it is not the one in front."""
        await self.query_one(page).write(content)
        tabs = self.query_one(TabbedContent)
        if tabs.active != self._pane(page):
            tabs.get_tab(self._pane(page)).label = self._label(page, unseen=True)

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
        await self._write(StderrWindow, str(error))

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
                await self._write(StdoutWindow, Text.from_markup(f"{first}\n{second}"))
            case Message(text=text, level="warning"):
                await self._write(StderrWindow, text)
            case Message(text=text):
                await self._write(StdoutWindow, text)
            case Output(stream="stdout", text=text):
                await self._write(StdoutWindow, text)
            case Output(stream="stderr", text=text):
                await self._write(StderrWindow, text)
            case Output(stream="ai", text=text):
                await self._write(AiWindow, text)
            case RunFinished():
                status.finished()
            case RunFailed():
                pass  # the error is shown once, when `_run` catches it

    async def ask(self, question: Question) -> str:
        """Stop the flow until the person has chosen an answer."""
        answer: str = await self.push_screen_wait(QuestionScreen(question))
        await self._write(
            StdoutWindow, Text.from_markup(f"[dim]{question.text}[/] [bold]{answer}[/]")
        )
        return answer
