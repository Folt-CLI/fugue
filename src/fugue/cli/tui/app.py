"""The screen `fugue work` runs in."""

from pathlib import Path
from typing import ClassVar

from rich.console import RenderableType
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.widgets import Footer, Header, TabbedContent, TabPane

from fugue.cli.tui.action import ActionWindow
from fugue.cli.tui.error_screen import ErrorScreen
from fugue.cli.tui.form_screen import FormScreen
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
from fugue.core.flow import StepOutline
from fugue.core.reporting import (
    AgentFinished,
    AgentStarted,
    Answer,
    Event,
    Form,
    Message,
    OutlineChanged,
    Output,
    PartFinished,
    PartStarted,
    Question,
    RunFinished,
    RunResumed,
    RunState,
    RunStopped,
    StatusMoved,
    StepFailed,
    StepFinished,
    StepStarted,
    StopReason,
    TicketDetails,
    TicketRead,
    WorktreeReady,
)
from fugue.core.work import Work, WorkResult
from fugue.errors import FlowError, FlowStopped, FugueError

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
        Binding("r", "answer", "Answer"),
        Binding("escape", "toggle_question", "Hide the question", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, work: Work, number: int) -> None:
        super().__init__()
        self._work = work
        self._number = number
        self.result: WorkResult | None = None
        self.error: FugueError | None = None
        self.state = RunState.RUNNING
        self.stop_message: str | None = None  # why the run is stopped, while it is

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="windows"):
            yield ChecklistWindow(self._work.steps())
            with TabbedContent(id="pages", initial=self._pane(TicketWindow)):
                for page in PAGES:
                    with TabPane(self._label(page), id=self._pane(page)):
                        yield page()
        yield ActionWindow()
        yield StatusBar()
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"fugue work #{self._number}"
        self.run_worker(self._run(), exit_on_error=False)

    async def action_answer(self) -> None:
        """Call up the question the run is waiting on, and focus it."""
        actions = self.query_one(ActionWindow)
        if actions.waiting:
            actions.show()

    async def action_toggle_question(self) -> None:
        """Escape puts a pending question away, so the other windows can be read; it brings
        the question back when it is already away."""
        actions = self.query_one(ActionWindow)
        if not actions.waiting:
            return
        if actions.is_open:
            actions.hide()
            self.set_focus(None)
        else:
            actions.show()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """The `r` key is only offered while the run is waiting on a question."""
        if action == "answer":
            return self.query_one(ActionWindow).waiting
        return True

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
        self._mark(page)

    def _mark(self, page: type[Window]) -> None:
        """Put a dot on a page's tab if it got something while another page is in front."""
        tabs = self.query_one(TabbedContent)
        if tabs.active != self._pane(page):
            tabs.get_tab(self._pane(page)).label = self._label(page, unseen=True)

    async def _run(self) -> None:
        try:
            self.result = await self._work.run(self._number, TuiReporter(self))
        except FlowStopped as stopped:
            self.stop_message = str(stopped)
        except FugueError as error:
            await self._fail(error)
        except Exception as error:  # a worker would swallow this and leave the screen running
            await self._fail(FlowError(f"{type(error).__name__}: {error}"))

    async def _fail(self, error: FugueError) -> None:
        self.error = error
        self.state = RunState.STOPPED
        self.query_one(StatusBar).failed(str(error))
        await self._write(StderrWindow, str(error))
        self.push_screen(ErrorScreen(str(error)))

    async def show(self, event: Event) -> None:
        """Show one event from the flow in the window it belongs to."""
        checklist = self.query_one(ChecklistWindow)
        status = self.query_one(StatusBar)
        if event.state is not None:
            self.state = event.state
        match event:
            case StepStarted(step=step):
                checklist.start(step)
                status.running(step, checklist.position(step), checklist.total)
            case StepFinished(step=step, detail=detail):
                checklist.finish(step, detail)
            case StepFailed(step=step):
                checklist.fail(step)
            case PartStarted(step=step, part=part):
                checklist.start_part(step, part)
            case PartFinished(step=step, part=part, ok=ok, detail=detail):
                checklist.finish_part(step, part, ok=ok, detail=detail)
            case TicketRead(repository=repository, ticket=ticket):
                await self.query_one(TicketWindow).show(ticket, repository)
            case OutlineChanged(steps=steps):
                checklist.set_outline([StepOutline(s.name, s.parts) for s in steps])
            case TicketDetails(fields=fields):
                await self.query_one(TicketWindow).details(fields)
            case StatusMoved(change=change):
                self.query_one(TicketWindow).set_field(change.field, change.value)
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
            case AgentStarted(agent=agent, role=role):
                await self.query_one(AiWindow).start(agent, role)
                self._mark(AiWindow)
            case AgentFinished(agent=agent, failed=failed):
                self.query_one(AiWindow).finish(agent, failed=failed)
            case Output(stream="ai", text=text, agent=agent):
                await self.query_one(AiWindow).write(agent, text)
                self._mark(AiWindow)
            case RunStopped(reason=reason, message=message):
                self.stop_message = message
                status.stopped(message, resumable=reason is StopReason.DECLINED)
            case RunResumed():
                self.stop_message = None
                status.resumed()
            case RunFinished():
                status.finished()

    async def ask(self, question: Question) -> Answer:
        """Stop the flow until the person has answered. A form opens its own window; every other
        kind of question is asked in the window at the bottom."""
        if isinstance(question, Form):
            answer = await self.push_screen_wait(FormScreen(question))
        else:
            self.refresh_bindings()
            try:
                answer = await self.query_one(ActionWindow).ask(question, returning_to=self.focused)
            finally:
                self.refresh_bindings()
        shown = Text.assemble((f"{question.text} ", "dim"), (answer.text or "(nothing)", "bold"))
        await self._write(StdoutWindow, shown)
        return answer
