"""Run and record an interview before publishing Question analysis."""

from collections.abc import Callable
from dataclasses import asdict

from rich.console import RenderableType

from fugue.agents import AccessMode, AgentRequest, AgentResult
from fugue.analysis.stage import analyse
from fugue.cli.chat import default_reader
from fugue.engine import Engine, EngineDeps, RunOutcome
from fugue.engine.pipeline import model_choice, role_adapter, usage_choice
from fugue.interview.prompts import ANALYST_PROMPT, STAND_IN_PROMPT, STAND_IN_SCHEMA
from fugue.interview.record import InterviewRecord
from fugue.interview.render import render_conversation
from fugue.interview.session import InterviewSession
from fugue.interview.store import InterviewStore
from fugue.models import IssueType, Role, Size, Status, Ticket
from fugue.questions.parse import parse_analysis
from fugue.workflow import RunState


async def interview_then_analyse(
    deps: EngineDeps,
    ticket: Ticket,
    *,
    automatic: bool = False,
    reader: Callable[[str], str] = default_reader,
    print_fn: Callable[[RenderableType], None] = print,
) -> RunOutcome:
    if ticket.issue_type != IssueType.QUESTION:
        raise ValueError("Interview requires a Question")
    if (
        ticket.status not in {Status.TO_DO, Status.IN_ANALYSIS}
        or parse_analysis("## Analysis\n" + ticket.body_sections.get("Analysis", "")).options
    ):
        raise ValueError("Interview requires pending Analysis")
    run_id = Engine(deps)._find_or_create_run(ticket.number).id
    operation = deps.store.begin_operation(run_id, "interview", str(ticket.number), {})
    deps.store.set_run_state(run_id, RunState.RUNNING)
    store = InterviewStore(deps.state_dir)
    record = store.load(deps.github.repo, ticket.number) or InterviewRecord(
        repo=deps.github.repo,
        ticket=ticket.number,
        mode="automatic" if automatic else "human",
    )
    if record.status == "proceeded":
        deps.store.complete_operation(operation.id, {"status": "proceeded"})
        return await analyse(deps, ticket, run_id=run_id)
    choices = {
        role: model_choice(deps, run_id, role, Size.L)
        for role in ([Role.ANALYST, Role.STAND_IN] if automatic else [Role.ANALYST])
    }
    analyst = role_adapter(deps, run_id, Role.ANALYST, deps.config.roles[Role.ANALYST])
    standin = (
        role_adapter(deps, run_id, Role.STAND_IN, deps.config.roles[Role.STAND_IN])
        if automatic
        else None
    )
    if automatic:
        record.standin_provider = deps.config.roles[Role.STAND_IN].provider
    glossary_path = deps.repo_root / "GLOSSARY.md"
    glossary = glossary_path.read_text(encoding="utf-8") if glossary_path.exists() else ""

    def request(role: Role, text: str) -> AgentRequest:
        choice = choices[role]
        config = deps.config.roles[role]
        return AgentRequest(
            role=role,
            prompt=(
                (ANALYST_PROMPT if role == Role.ANALYST else STAND_IN_PROMPT)
                + f"\nTicket #{ticket.number} data:\nSummary:\n"
                + ticket.body_sections.get("Summary", "")
                + "\nScope:\n"
                + ticket.body_sections.get("Scope", "")
                + "\nGLOSSARY.md data:\n"
                + glossary
                + "\nConversation data:\n"
                + render_conversation(record)
            ),
            cwd=deps.repo_root,
            access=AccessMode.READ_ONLY,
            output_schema=STAND_IN_SCHEMA if role == Role.STAND_IN else None,
            timeout_seconds=deps.config.limits.interview_call_timeout_seconds,
            model=choice.models.get(config.provider),
            models=choice.models,
        )

    def usage(role: Role, result: AgentResult) -> None:
        values = asdict(result.usage) if result.usage is not None else {}
        selection = usage_choice(choices[role], result.provider)
        deps.store.record_usage(
            run_id, role, result.provider, result.authentication_mode, **values, **selection
        )
        record.usage.append(
            {
                "role": role.value,
                "provider": result.provider,
                **values,
                **selection,
            }
        )

    session = InterviewSession(
        record,
        store,
        analyst,
        request,
        limits=deps.config.limits,
        standin=standin,
        reader=reader,
        print_fn=print_fn,
        on_result=usage,
    )
    state = await session.run(automatic=automatic)
    if session.record.status == "proceeded":
        deps.store.complete_operation(operation.id, {"status": "proceeded"})
        return await analyse(deps, ticket, run_id=run_id)
    deps.store.set_run_state(run_id, state)
    print_fn(f"next: fugue question {ticket.number}")
    return RunOutcome(run_id, state, problems=(session.reason,) if session.reason else ())
