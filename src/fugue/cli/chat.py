"""Reusable chat component: streaming output, an input line, slash commands, a transcript."""

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext, suppress
from pathlib import Path

from rich.console import Console, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from fugue.agents import AgentEvent, AgentRequest, ProviderAdapter
from fugue.cli import terminal

HELP = (
    "Commands:\n"
    "  /help        list commands\n"
    "  /save PATH   save the transcript to a new UTF-8 text file\n"
    "  /quit        end the chat\n"
    "End a line with a backslash to continue it on the next line."
)

_readline_loaded = False


def default_reader(prompt: str) -> str:
    """Read one line; line editing and history come from readline when it is available."""
    global _readline_loaded
    if not _readline_loaded:
        _readline_loaded = True
        with suppress(ImportError):
            import readline  # noqa: F401  # imported for its effect on input()
    return input(prompt)


class ChatSession:
    """A conversation with an adapter.

    `emit` shows whole messages. `write` receives streamed text chunks as they arrive;
    without it the streamed text is shown once through `emit` at the end. With `live` and a
    `console`, streamed text renders as Markdown in place instead.
    """

    def __init__(
        self,
        adapter: ProviderAdapter,
        make_request: Callable[[str], AgentRequest],
        *,
        reader: Callable[[str], str] = default_reader,
        emit: Callable[[RenderableType], None] | None = None,
        write: Callable[[str], None] | None = None,
        console: Console | None = None,
        live: bool = False,
        prompt: str = "you> ",
        continuation: str = "... ",
    ) -> None:
        self.adapter = adapter
        self.make_request = make_request
        self.reader = reader
        self.console = console
        self.emit: Callable[[RenderableType], None] = (
            emit or (console if console is not None else terminal.console_for()).print
        )
        self.write = write
        self.live = live and console is not None
        self.prompt = prompt
        self.continuation = continuation
        self.turns: list[tuple[str, str]] = []

    @property
    def transcript(self) -> str:
        return "".join(f"you: {question}\n\n{answer}\n\n" for question, answer in self.turns)

    async def ask(self, question: str) -> bool:
        """Send one question; stream the answer. False when the adapter reported a failure."""
        chunks: list[str] = []
        display: Live | None = None

        async def on_event(event: AgentEvent) -> None:
            if event.kind != "text" or not event.text:
                return
            chunks.append(event.text)
            if display is not None:
                display.update(Markdown("".join(chunks)))
            elif self.write is not None:
                self.write(event.text)

        request = self.make_request(question)
        scope: AbstractContextManager[Live | None]
        if self.live and self.console is not None:
            scope = Live(Markdown(""), console=self.console, refresh_per_second=8)
        else:
            scope = nullcontext()
        with scope as entered:
            display = entered
            result = await self.adapter.run(request, on_event)
        streamed = "".join(chunks)
        if not result.ok:
            self.emit(Text(f"Follow-up failed: {result.error}"))
            return False
        if not streamed:
            self.emit(Text(result.final_text))
            streamed = result.final_text
        elif display is None:
            if self.write is None:
                self.emit(Text(streamed))
            elif not streamed.endswith("\n"):
                self.write("\n")
        self.turns.append((question, streamed))
        return True

    def read_message(self) -> str:
        """One message; a trailing backslash continues it. EOFError ends the chat."""
        lines: list[str] = []
        prompt = self.prompt
        while True:
            line = self.reader(prompt)
            if not line.endswith("\\"):
                lines.append(line)
                return "\n".join(lines)
            lines.append(line[:-1])
            prompt = self.continuation

    def save(self, target: str) -> str:
        """Write the transcript to a new file; the returned text is the user-facing outcome."""
        if not target:
            return "Usage: /save PATH"
        path = Path(target).expanduser()
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(self.transcript)
        except FileExistsError:
            return f"Refusing to overwrite existing file: {path}"
        except OSError as error:
            return f"Could not save transcript: {error}"
        return f"Saved transcript to {path}"

    async def run(self) -> None:
        """Read messages until /quit or end of input."""
        while True:
            try:
                message = self.read_message()
            except EOFError:
                return
            text = message.strip()
            if not text:
                continue
            if text.startswith("/"):
                command, _, argument = text.partition(" ")
                if command == "/quit":
                    return
                if command == "/help":
                    self.emit(Text(HELP))
                elif command == "/save":
                    self.emit(Text(self.save(argument.strip())))
                else:
                    self.emit(Text(f"Unknown command {command}; /help lists commands."))
                continue
            try:
                await self.ask(text)
            except Exception as error:
                self.emit(Text(f"Follow-up failed: {error}"))
