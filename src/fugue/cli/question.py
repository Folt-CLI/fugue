"""Interactive Question walkthrough with injectable terminal functions."""

import asyncio
import re
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import partial
from io import StringIO
from pathlib import Path

from rich.console import RenderableType
from rich.text import Text

from fugue.agents import (
    AccessMode,
    AdapterRegistry,
    AgentRequest,
    Capability,
    ProviderAdapter,
    register_claude,
    register_codex,
    resolve_models,
)
from fugue.cli import terminal, wiring
from fugue.cli.chat import ChatSession, default_reader
from fugue.cli.choices import Chooser, make_chooser
from fugue.config import FugueConfig
from fugue.models import IssueType, Role, Size, Status, Ticket
from fugue.questions.decision import QuestionClient, QuestionGitHubClient, record_decision
from fugue.questions.parse import Option, parse_analysis


def analysis_pending(ticket: Ticket, body: str) -> bool:
    """Whether a Question can begin or continue its interview."""
    return (
        ticket.issue_type == IssueType.QUESTION
        and ticket.status in {Status.TO_DO, Status.IN_ANALYSIS}
        and not parse_analysis(body).options
    )


class QuestionWalkthrough:
    def __init__(
        self,
        client: QuestionClient,
        repo_root: Path,
        *,
        adapter: ProviderAdapter | None = None,
        config: FugueConfig | None = None,
        prompt: Callable[[str], str] = default_reader,
        print_fn: Callable[[RenderableType], None] | None = None,
        chooser: Chooser | None = None,
    ) -> None:
        self.client = client
        self.repo_root = repo_root
        self.adapter = adapter
        self.config = config or FugueConfig()
        self.prompt = prompt
        self._chooser = chooser
        self._console = None if print_fn else terminal.console_for()
        self.print: Callable[[RenderableType], None] = print_fn or self._emit

    def _emit(self, content: RenderableType) -> None:
        console = self._console or terminal.console_for()
        if isinstance(content, str):
            terminal.print_text(content, console=console)
        else:
            console.print(content)

    def _markdown(self, text: str) -> None:
        if self._console is not None:
            terminal.print_markdown(text, console=self._console)
        else:
            self.print(text)

    def _selected(self, number: int, option: int) -> None:
        self.print(f"Selected option {option}.")
        self.print(f"next: fugue children {number}")

    def _show_option(self, option: Option) -> None:
        self._markdown(
            f"### Option {option.number}: {option.name}\n\n"
            f"**You get**\n\n{option.you_get or 'Not provided.'}\n\n"
            f"**Changes**\n\n{option.changes or 'Not provided.'}\n\n"
            f"**Watch out**\n\n{option.watch_out or 'Not provided.'}\n\n"
            f"**Implementation scope:** [{option.scope_name}]({option.scope_url})"
        )

    async def run(
        self,
        number: int,
        select: int | None = None,
        *,
        reason: str | None = None,
        by: str | None = None,
    ) -> None:
        ticket = await self.client.get_ticket(number)
        body = await self.client.get_body(number)
        analysis = parse_analysis(body)
        is_question = ticket.issue_type == IssueType.QUESTION
        label = "Question" if is_question else "Ticket"
        if not analysis.options:
            raise ValueError(f"{label} #{number} has no Analysis options")
        menu = (
            select is None
            and sys.stdin.isatty()
            and terminal.is_interactive(self._console or terminal.console_for())
        )
        if menu:
            recommended = re.match(r"Option ([1-9][0-9]*):", analysis.recommendation)
            recommended_index = next(
                (
                    index
                    for index, option in enumerate(analysis.options)
                    if recommended and option.number == int(recommended[1])
                ),
                None,
            )
            output = StringIO()
            terminal.print_summary(
                f"{label} #{number}: {ticket.title}",
                analysis.overview,
                [option.name for option in analysis.options],
                recommended_index=recommended_index,
                console=self._console or terminal.console_for(file=output),
            )
            if self._console is None:
                self.print(Text(output.getvalue().rstrip("\n")))
        else:
            self._markdown(f"# {label} #{number}: {ticket.title}\n\n{analysis.overview}")
            for option in analysis.options:
                self.print(f"{option.number}: {option.name}")
            self._markdown(f"### Recommendation\n\n{analysis.recommendation or 'Not provided.'}")
        if select is not None:
            await record_decision(
                self.client, number, body, analysis.option(select), reason=reason, by=by
            )
            self._selected(number, select)
            return
        chooser = (self._chooser or make_chooser()) if menu else None
        recommended = re.match(r"Option ([1-9][0-9]*):", analysis.recommendation)
        labels = [
            f"Option {option.number}: {option.name}"
            + (" (recommended)" if recommended and int(recommended[1]) == option.number else "")
            for option in analysis.options
        ]
        while True:
            try:
                if chooser is not None:
                    action = await asyncio.to_thread(
                        chooser.choose,
                        "Question walkthrough",
                        [
                            *labels,
                            "Compare options",
                            "Ask a follow-up",
                            "Select an option",
                            "Show full analysis",
                            "Quit",
                        ],
                    )
                    if action is None:
                        return
                    if action < len(labels):
                        command = str(analysis.options[action].number)
                    else:
                        command = ("c", "a", "select", "full", "q")[action - len(labels)]
                    if command == "select":
                        selection = await asyncio.to_thread(
                            chooser.choose, "Select an option", labels
                        )
                        if selection is None:
                            return
                        selected = analysis.options[selection]
                        confirmed = await asyncio.to_thread(
                            chooser.confirm, f"Select option {selected.number}: {selected.name}?"
                        )
                        if not confirmed:
                            continue
                        selected_reason = reason
                        if selected_reason is None:
                            selected_reason = self.prompt("Reason (optional): ").strip() or None
                        await record_decision(
                            self.client, number, body, selected, reason=selected_reason, by=by
                        )
                        self._selected(number, selected.number)
                        return
                else:
                    command = self.prompt("Option, c compare, a ask, s N select, q quit: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if command == "q":
                return
            if chooser is not None and command == "full":
                self._markdown(analysis.text)
            elif command == "c":
                rows = [
                    [
                        f"{option.number}: {option.name}",
                        option.you_get,
                        option.changes,
                        option.watch_out,
                    ]
                    for option in analysis.options
                ]
                header = ["Option", "You get", "Changes", "Watch out"]
                if self._console is not None:
                    terminal.print_table(
                        header, rows, title="Compare options", console=self._console
                    )
                else:
                    self.print("Compare options")
                    for line in terminal.table_text(header, rows):
                        self.print(line)
            elif command == "a":
                if self.adapter is None:
                    self.print("No analyst adapter configured; follow-up skipped.")
                    continue
                if Capability.READ_ONLY_MODE not in self.adapter.capabilities:
                    self.print("Analyst adapter lacks read-only mode; follow-up skipped.")
                    continue
                try:
                    question = self.prompt("Follow-up question: ").strip()
                except (EOFError, KeyboardInterrupt):
                    return
                if not question:
                    self.print("Enter a non-empty follow-up question.")
                    continue
                role_config = self.config.roles.get(Role.ANALYST)
                # Every Question is Size L; there is no run, so the choice is not persisted.
                choice = resolve_models(self.config, Role.ANALYST, Size.L)
                console = self._console
                model = choice.models.get(role_config.provider) if role_config else None

                def make_request(
                    text: str,
                    model: str | None = model,
                    models: Mapping[str, str | None] = choice.models,
                ) -> AgentRequest:
                    return AgentRequest(
                        role=Role.ANALYST,
                        prompt=f"Question #{number} Analysis:\n{analysis.text}\n\n"
                        f"Follow-up question:\n{text}\n\n"
                        "Answer read-only; do not modify files.",
                        cwd=self.repo_root,
                        access=AccessMode.READ_ONLY,
                        model=model,
                        models=models,
                        timeout_seconds=self.config.limits.agent_timeout_seconds,
                    )

                session = ChatSession(
                    self.adapter,
                    make_request,
                    emit=self.print,
                    write=partial(console.out, end="") if console else None,
                    console=console,
                    live=console is not None and terminal.is_interactive(console),
                )
                await session.ask(question)
            elif command.isascii() and command.isdecimal():
                try:
                    self._show_option(analysis.option(int(command)))
                except ValueError as error:
                    self.print(str(error))
            elif command.startswith("s "):
                try:
                    selected = analysis.option(int(command[2:].strip()))
                except ValueError:
                    self.print("Select an available option using s N.")
                    continue
                await record_decision(self.client, number, body, selected, reason=reason, by=by)
                self._selected(number, selected.number)
                return
            else:
                self.print("Use an option number, c, a, s N or q.")


def build_question(config: FugueConfig, repo_root: Path) -> QuestionWalkthrough:
    github = config.github
    if github.project_owner is None or github.project_number is None:
        raise wiring.WiringError("github.project_owner and github.project_number are required")
    registry = AdapterRegistry()
    register_claude(registry)
    register_codex(registry)
    role_config = config.roles.get(Role.ANALYST)
    return QuestionWalkthrough(
        QuestionGitHubClient(
            wiring.resolve_repo(config, repo_root),
            github.project_owner,
            github.project_number,
            resolve_token_env=github.resolve_token_env,
        ),
        repo_root,
        adapter=(
            registry.build_for_role(
                role_config,
                clock=lambda: datetime.now(UTC),
                cooldown_seconds=config.limits.provider_cooldown_seconds,
            )
            if role_config
            else None
        ),
        config=config,
    )
