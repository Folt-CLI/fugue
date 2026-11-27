"""The window for the agents: a bar down the side lists them, the rest shows the one selected."""

from dataclasses import dataclass, field
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import ContentSwitcher, OptionList, Static
from textual.widgets.option_list import Option

from fugue.cli.tui.windows.window import Window

EMPTY = "agents-empty"


class AgentList(OptionList, inherit_bindings=False):
    """The agents, one line each. Up and down (or k and j) move along it, and the log in front
    changes with them. Paging keys are left to the window, which scrolls the log with them;
    left and right are left to it too, to move between the list and the log."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("down", "cursor_down", "Next agent", key_display="↑↓"),
        Binding("up", "cursor_up", "Previous agent", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("home", "first", "First", show=False),
        Binding("end", "last", "Last", show=False),
    ]


class AgentLog(VerticalScroll, inherit_bindings=False):
    """What one agent wrote, a line at a time. Lines wrap at the width the log has when it is
    shown and again when the window is resized, which is why they are widgets and not
    pre-wrapped text (a log written while hidden would be wrapped at the wrong width). It
    follows new lines while it is at the bottom, and stays put once scrolled up. Up and down
    (or k and j) scroll it; left and right are left to the window, to move back to the list."""

    MAX_LINES: ClassVar[int] = 5000

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("down", "scroll_down", "Scroll", key_display="↑↓"),
        Binding("up", "scroll_up", "Scroll", show=False),
        Binding("j", "scroll_down", "Down", show=False),
        Binding("k", "scroll_up", "Up", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("home", "scroll_home", "Top", show=False),
        Binding("end", "scroll_end", "Bottom", show=False),
    ]

    following = True

    def write(self, text: str) -> None:
        """Add a line at the end."""
        if self.size.height:  # a hidden log cannot be scrolled, so it keeps its last state
            self.following = self.scroll_y >= self.max_scroll_y - 1
        self.mount(Static(Text(text), classes="agent-line"))
        for old in list(self.children)[: -self.MAX_LINES]:
            old.remove()
        if self.following:
            self.call_after_refresh(self.scroll_end, animate=False)

    def on_show(self) -> None:
        """Lines written while the log was hidden: go to the end if it was following."""
        if self.following:
            self.call_after_refresh(self.scroll_end, animate=False)


@dataclass
class Agent:
    """One agent session shown in the window."""

    name: str
    index: int
    role: str = ""
    state: str = "running"  # running, done or failed
    unseen: bool = False
    log: "AgentLog" = field(default_factory=lambda: AgentLog())


class AiWindow(Window):
    """Several agents can run in one flow, so each gets a line in a list at the left and a log
    of its own at the right. The list shows who is running, done or failed, and marks an agent
    that wrote something while another one was in front.

    The list has focus when the page opens, so the arrow keys switch agents; Page Up and Page
    Down scroll the log in front from there. Right (or `l`) moves focus into the log, where the
    arrow keys scroll it, and left (or `h`) moves back. `[` and `]` change agent from either.

    Agents are never declared up front: the first thing that mentions a name creates it.
    """

    title = "AI"
    key = "a"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("[", "agent(-1)", "Previous agent", key_display="[ ]"),
        Binding("]", "agent(1)", "Next agent", show=False),
        Binding("left", "pane('list')", "List", show=False),
        Binding("h", "pane('list')", "List", show=False),
        Binding("right", "pane('log')", "Log", key_display="←→"),
        Binding("l", "pane('log')", "Log", show=False),
        Binding("pageup", "log('page_up')", "Page up", show=False),
        Binding("pagedown", "log('page_down')", "Page down", show=False),
    ]

    FRAMES: ClassVar[str] = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        super().__init__()
        self.can_focus = False
        self._agents: list[Agent] = []
        self._by_name: dict[str, Agent] = {}
        self._frame = 0
        self._list = AgentList(id="agent-list")
        self._logs = ContentSwitcher(initial=EMPTY, id="agent-logs")

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield self._list
            with self._logs:
                yield Static("The agent has not started.", id=EMPTY, classes="placeholder")

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)

    def focus(self, scroll_visible: bool = True) -> "AiWindow":
        """Focus goes to the agent list, so the arrow keys switch agents."""
        self._list.focus(scroll_visible)
        return self

    async def start(self, name: str, role: str = "") -> None:
        agent = await self._agent(name)
        agent.role = role or agent.role
        agent.state = "running"
        self._draw(agent)

    async def write(self, name: str, text: str) -> None:
        agent = await self._agent(name)
        agent.log.write(text)
        if agent is not self._shown():
            agent.unseen = True
            self._draw(agent)

    def finish(self, name: str, *, failed: bool = False) -> None:
        agent = self._by_name.get(name)
        if agent is not None:
            agent.state = "failed" if failed else "done"
            self._draw(agent)

    def action_agent(self, step: int) -> None:
        """Go to the previous or next agent, wrapping around."""
        if self._agents:
            shown = self._shown()
            at = self._agents.index(shown) if shown else 0
            self._list.highlighted = (at + step) % len(self._agents)

    def action_pane(self, pane: str) -> None:
        """Move focus to the agent list or to the log in front."""
        shown = self._shown()
        (shown.log if pane == "log" and shown else self._list).focus()

    def action_log(self, action: str) -> None:
        """Scroll the log in front, for the keys the list leaves to the window."""
        shown = self._shown()
        if shown is not None:
            getattr(shown.log, f"action_{action}")()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        agent = self._agents[event.option_index]
        self._logs.current = agent.log.id
        agent.unseen = False
        self._draw(agent)

    # ----- the list -----

    async def _agent(self, name: str) -> Agent:
        name = name or "agent"
        agent = self._by_name.get(name)
        if agent is None:
            agent = Agent(name=name, index=len(self._agents))
            agent.log.id = f"agent-log-{agent.index}"
            self._agents.append(agent)
            self._by_name[name] = agent
            await self._logs.mount(agent.log)
            self._list.add_option(Option(self._row(agent), id=str(agent.index)))
            if len(self._agents) == 1:
                self._list.highlighted = 0
        return agent

    def _shown(self) -> Agent | None:
        at = self._list.highlighted
        return self._agents[at] if at is not None and at < len(self._agents) else None

    def _row(self, agent: Agent) -> Text:
        match agent.state:
            case "running":
                mark, style = self.FRAMES[self._frame], "cyan"
            case "failed":
                mark, style = "✗", "bold red"
            case _:
                mark, style = "✓", "bold green"
        row = Text.assemble((f"{mark} ", style), (agent.name, "bold" if agent.unseen else ""))
        if agent.unseen:
            row.append(" •", style="bold yellow")
        if agent.role:
            row.append(f"\n  {agent.role}", style="dim")
        return row

    def _draw(self, agent: Agent) -> None:
        self._list.replace_option_prompt(str(agent.index), self._row(agent))

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(self.FRAMES)
        for agent in self._agents:
            if agent.state == "running":
                self._draw(agent)
