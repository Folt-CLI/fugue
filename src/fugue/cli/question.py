"""Interactive Question walkthrough with injectable terminal functions."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console, RenderableType
from rich.markdown import Markdown
from rich.table import Table

from fugue.agents import (
    AccessMode,
    AdapterRegistry,
    AgentRequest,
    Capability,
    ProviderAdapter,
    register_claude,
    register_codex,
)
from fugue.cli import wiring
from fugue.config import FugueConfig
from fugue.models import IssueType, Role
from fugue.questions.decision import QuestionClient, QuestionGitHubClient, record_decision
from fugue.questions.parse import Option, parse_analysis


class QuestionWalkthrough:
    def __init__(
        self,
        client: QuestionClient,
        repo_root: Path,
        *,
        adapter: ProviderAdapter | None = None,
        config: FugueConfig | None = None,
        prompt: Callable[[str], str] = input,
        print_fn: Callable[[RenderableType], None] | None = None,
    ) -> None:
        self.client = client
        self.repo_root = repo_root
        self.adapter = adapter
        self.config = config or FugueConfig()
        self.prompt = prompt
        self.print = print_fn or Console().print

    def _show_option(self, option: Option) -> None:
        self.print(
            Markdown(
                f"### Option {option.number}: {option.name}\n\n"
                f"**You get**\n\n{option.you_get or 'Not provided.'}\n\n"
                f"**Changes**\n\n{option.changes or 'Not provided.'}\n\n"
                f"**Watch out**\n\n{option.watch_out or 'Not provided.'}\n\n"
                f"**Implementation scope:** [{option.scope_name}]({option.scope_url})"
            )
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
        self.print(Markdown(f"# {label} #{number}: {ticket.title}\n\n{analysis.overview}"))
        for option in analysis.options:
            self.print(f"{option.number}: {option.name}")
        self.print(Markdown(f"### Recommendation\n\n{analysis.recommendation or 'Not provided.'}"))
        if select is not None:
            await record_decision(
                self.client, number, body, analysis.option(select), reason=reason, by=by
            )
            self.print(f"Selected option {select}.")
            return
        while True:
            try:
                command = self.prompt("Option, c compare, a ask, s N select, q quit: ").strip()
            except EOFError:
                return
            if command == "q":
                return
            if command == "c":
                table = Table(title="Compare options")
                table.add_column("Option")
                for label in ("You get", "Changes", "Watch out"):
                    table.add_column(label)
                for option in analysis.options:
                    table.add_row(
                        f"{option.number}: {option.name}",
                        option.you_get,
                        option.changes,
                        option.watch_out,
                    )
                self.print(table)
            elif command == "a":
                if self.adapter is None:
                    self.print("No analyst adapter configured; follow-up skipped.")
                    continue
                if Capability.READ_ONLY_MODE not in self.adapter.capabilities:
                    self.print("Analyst adapter lacks read-only mode; follow-up skipped.")
                    continue
                try:
                    question = self.prompt("Follow-up question: ").strip()
                except EOFError:
                    return
                if not question:
                    self.print("Enter a non-empty follow-up question.")
                    continue
                role_config = self.config.roles.get(Role.ANALYST)
                result = await self.adapter.run(
                    AgentRequest(
                        role=Role.ANALYST,
                        prompt=f"Question #{number} Analysis:\n{analysis.text}\n\n"
                        f"Follow-up question:\n{question}\n\n"
                        "Answer read-only; do not modify files.",
                        cwd=self.repo_root,
                        access=AccessMode.READ_ONLY,
                        model=role_config.model if role_config else None,
                        timeout_seconds=self.config.limits.agent_timeout_seconds,
                    )
                )
                self.print(result.final_text if result.ok else f"Follow-up failed: {result.error}")
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
                self.print(f"Selected option {selected.number}.")
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
