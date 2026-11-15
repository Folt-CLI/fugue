"""Interactive Question walkthrough with injectable terminal functions."""

import asyncio
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, cast

from rich.console import RenderableType

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
from fugue.children import apply
from fugue.children.propose import ProposalStore, add, build_request, remove, set_field, validate
from fugue.cli import terminal, wiring
from fugue.cli.chat import ChatSession, default_reader
from fugue.cli.choices import Chooser, make_chooser
from fugue.config import FugueConfig
from fugue.engine.facts import scope_url, selected_solution
from fugue.github import GitHubClient
from fugue.github.parse import parse_body_sections
from fugue.interview.store import InterviewStore
from fugue.models import IssueType, Role, Size, Status, Ticket
from fugue.questions.decision import QuestionClient, QuestionGitHubClient, record_decision
from fugue.questions.parse import Option, parse_analysis
from fugue.store import Store


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
        state_dir: Path | None = None,
    ) -> None:
        self.state_dir = state_dir or wiring.resolve_state_dir(
            config or FugueConfig(), os.environ, Path.home()
        )
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

    async def _selected(self, number: int, option: int, is_question: bool) -> None:
        self.print(f"Selected option {option}.")
        if is_question:
            await self._propose_children(number)

    async def _propose_children(self, number: int) -> None:
        client = cast(GitHubClient, self.client)
        saved = None
        try:
            question = await self.client.get_ticket(number)
            question = question.model_copy(
                update={"body_sections": parse_body_sections(await self.client.get_body(number))}
            )
            if question.issue_type != IssueType.QUESTION or not selected_solution(question):
                raise ValueError("Child proposals require a Question with [Selected Solution]")
            for field, value in (
                ("milestone", question.milestone),
                ("assignee", question.assignees),
            ):
                if not value:
                    raise ValueError(f"Question is missing {field}; use fugue children")
            repo = client.repo
            proposals = ProposalStore(self.state_dir)
            saved = proposals.load(repo, number)
            scope_dir = proposals.path(repo, number).with_suffix("")
            if saved is None:
                if self.adapter is None:
                    raise ValueError("No analyst adapter configured")
                if Capability.READ_ONLY_MODE not in self.adapter.capabilities:
                    raise ValueError("Analyst adapter lacks read-only mode")
                url = scope_url(question)
                if url is None:
                    raise ValueError("Selected solution has no scope attachment")
                scope_dir.mkdir(parents=True, exist_ok=True)
                scope = await client.download_attachment(url, scope_dir / "selected.md")
                record = InterviewStore(self.state_dir).load(repo, number)
                request = build_request(
                    question,
                    question.body_sections.get("Analysis", ""),
                    scope.read_text(encoding="utf-8"),
                    [turn.model_dump() for turn in record.turns] if record else [],
                    config=self.config,
                    cwd=self.repo_root,
                )
                reply = await self.adapter.run(request)
                if not reply.ok or reply.cancelled or reply.timed_out or reply.unsupported:
                    raise ValueError(reply.error or "Child proposal analyst call failed")
                raw = dict(reply.structured_output or json.loads(reply.final_text))
                if not isinstance(raw.get("children"), list) or not all(
                    isinstance(child, dict) for child in raw["children"]
                ):
                    raise ValueError("Analyst returned an unusable child proposal")
                saved = {
                    "children": raw["children"],
                    "locked": False,
                    "milestone": question.milestone,
                    "assignee": question.assignees[0],
                }
                proposals.save(repo, number, saved)
            chooser = (
                (self._chooser or make_chooser())
                if (
                    sys.stdin.isatty()
                    and terminal.is_interactive(self._console or terminal.console_for())
                )
                else None
            )
            while True:
                self.print(
                    f"Child proposal: parent Question #{number}, milestone "
                    f"{saved['milestone']}, assignee {saved['assignee']}"
                )
                headers = ["Key", "Title", "Type", "Size", "Priority", "Components", "Blocked by"]
                rows = [
                    [
                        str(child.get(field, ""))
                        for field in (
                            "key",
                            "title",
                            "issue_type",
                            "size",
                            "priority",
                            "components",
                            "blocked_by",
                        )
                    ]
                    for child in saved["children"]
                ]
                for line in terminal.table_text(headers, rows):
                    self.print(line)
                planned = None
                try:
                    planned = validate(
                        saved,
                        question,
                        saved["milestone"],
                        saved["assignee"],
                        scope_dir,
                        self.config.policy,
                    )
                except (ValueError, OSError, TypeError, KeyError) as error:
                    self.print(str(error))
                if planned is not None and saved.get("locked") and not saved.get("numbers"):
                    store = wiring.open_store(self.state_dir)
                    try:
                        completed_records = store._connection.execute(
                            "SELECT o.intent_json, o.result_json FROM operations o "
                            "JOIN runs r ON r.id = o.run_id WHERE r.repo = ? "
                            "AND r.ticket_number = ? AND o.kind = 'children_plan' "
                            "AND o.status = 'done'",
                            (repo, number),
                        ).fetchall()
                        for record in completed_records:
                            if (
                                json.loads(record["intent_json"])["fingerprint"]
                                == planned.fingerprint
                            ):
                                saved["numbers"] = json.loads(record["result_json"])["children"]
                                proposals.save(repo, number, saved)
                                break
                    finally:
                        store.close()
                if saved.get("numbers"):
                    self._created(saved["numbers"], saved)
                    return
                if saved.get("locked"):
                    self.print("Creation began; edits to this child proposal are refused.")
                try:
                    if chooser is not None:
                        choices = ["Edit a child", "Remove a child", "Add a child"]
                        if planned is not None:
                            choices.append("Confirm and create")
                        choices.append("Save and quit")
                        index = await asyncio.to_thread(
                            chooser.choose, "Review child proposal", choices
                        )
                        action = choices[index] if index is not None else "Save and quit"
                        if action == "Confirm and create":
                            command = (
                                "yes"
                                if await asyncio.to_thread(
                                    chooser.confirm, "Create these children?", default=False
                                )
                                else "q"
                            )
                        elif action == "Save and quit":
                            command = "q"
                        elif action == "Add a child":
                            command = "a"
                        else:
                            key = self.prompt("Child key: ").strip()
                            command = (
                                "r " + key
                                if action == "Remove a child"
                                else (
                                    "e "
                                    + key
                                    + " "
                                    + self.prompt("Field (scope_text to view/replace): ").strip()
                                    + " "
                                    + self.prompt(
                                        "Text (empty scope_text to view, @PATH to replace): "
                                    )
                                )
                            )
                    else:
                        command = self.prompt("e KEY FIELD TEXT, r KEY, a, yes, q: ").strip()
                    if command == "yes":
                        if planned is None:
                            self.print("Resolve the child proposal problems before creation.")
                            continue
                        saved["locked"] = True
                        proposals.save(repo, number, saved)
                        # Revalidate the locked input immediately before applying it.
                        planned = validate(
                            saved,
                            question,
                            saved["milestone"],
                            saved["assignee"],
                            scope_dir,
                            self.config.policy,
                        )
                        database = self.state_dir / "fugue.db"
                        if not database.exists():
                            Store(database).close()
                        store = wiring.open_store(self.state_dir)
                        try:
                            try:
                                saved["numbers"] = await apply(planned, client, store)
                            except ValueError as error:
                                self.print(str(error))
                                self.print("next: fugue children FILE")
                                return
                        finally:
                            store.close()
                        proposals.save(repo, number, saved)
                        self._created(saved["numbers"], saved)
                        return
                    if command.startswith("e "):
                        _, key, field, *rest = command.split(" ", 3)
                        text = rest[0] if rest else ""
                        if field == "scope_text" and not text:
                            child = next(c for c in saved["children"] if c.get("key") == key)
                            self._markdown(child.get("scope_text", "Not provided."))
                        else:
                            set_field(saved, key, field, text)
                    elif command.startswith("r "):
                        for note in remove(saved, command[2:].strip()):
                            self.print(note)
                    elif command == "a":
                        if saved.get("locked"):
                            raise ValueError(
                                "Creation began; edits to this child proposal are refused"
                            )
                        add(saved, json.loads(self.prompt("Child JSON (all proposal fields): ")))
                    else:
                        break
                    proposals.save(repo, number, saved)
                except (EOFError, KeyboardInterrupt):
                    break
                except (ValueError, OSError, KeyError, StopIteration) as error:
                    self.print(str(error))
            proposals.save(repo, number, saved)
            self.print(f"next: fugue question {number}")
        except (EOFError, KeyboardInterrupt):
            self.print(f"next: fugue question {number}")
        except Exception as error:
            self.print(str(error))
            self.print(
                f"next: fugue question {number}"
                if saved and saved.get("locked")
                else "next: fugue children FILE"
            )

    def _created(self, numbers: dict[str, int], proposal: dict[str, Any]) -> None:
        for key, number in numbers.items():
            self.print(f"{key}: #{number}")
        for child in proposal["children"]:
            if not child.get("blocked_by") and child.get("size") in {"XS", "S"}:
                self.print(f"next: fugue run {numbers[child['key']]}")
                break

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
        self._markdown(f"# {label} #{number}: {ticket.title}\n\n{analysis.overview}")
        for option in analysis.options:
            self.print(f"{option.number}: {option.name}")
        self._markdown(f"### Recommendation\n\n{analysis.recommendation or 'Not provided.'}")
        if select is not None:
            await record_decision(
                self.client, number, body, analysis.option(select), reason=reason, by=by
            )
            self.print(f"Selected option {select}.")
            if is_question:
                self.print(f"next: fugue question {number}")
            return
        menu = sys.stdin.isatty() and terminal.is_interactive(
            self._console or terminal.console_for()
        )
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
                        [*labels, "Compare options", "Ask a follow-up", "Select an option", "Quit"],
                    )
                    if action is None:
                        return
                    if action < len(labels):
                        command = str(analysis.options[action].number)
                    else:
                        command = ("c", "a", "select", "q")[action - len(labels)]
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
                        await self._selected(number, selected.number, is_question)
                        return
                else:
                    command = self.prompt("Option, c compare, a ask, s N select, q quit: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if command == "q":
                return
            if command == "c":
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
                await self._selected(number, selected.number, is_question)
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
        state_dir=wiring.resolve_state_dir(config, os.environ, Path.home()),
    )
