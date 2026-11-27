"""An M Question answers itself: analysis, selection by policy, then its children.

No interview and no confirmation: the Size rule selects the recommendation when no trigger
fired, and the children are created at once. A fired trigger parks the Question for the
developer, as for any other analysis.
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fugue.agents import AccessMode, Capability
from fugue.analysis.stage import analyse
from fugue.children import apply
from fugue.children.propose import ProposalStore, build_request, validate
from fugue.config import RepoPolicy
from fugue.engine import Engine, EngineDeps, RunOutcome
from fugue.engine.facts import scope_url, selected_solution
from fugue.engine.pipeline import model_choice, role_adapter, usage_choice
from fugue.github import GitHubClient
from fugue.models import AnalysisDepth, IssueType, Role, Selection, Ticket
from fugue.workflow import RunState


def is_automatic(ticket: Ticket, policy: RepoPolicy) -> bool:
    """A Question whose Size analyses in full and selects by policy needs no interview."""
    rule = policy.size_rules[ticket.size]
    return (
        ticket.issue_type == IssueType.QUESTION
        and rule.analysis == AnalysisDepth.FULL
        and rule.selection == Selection.POLICY
    )


def _blocked(run_id: int, *problems: str) -> RunOutcome:
    return RunOutcome(run_id, RunState.BLOCKED, problems=problems)


async def answer_question(deps: EngineDeps, ticket: Ticket) -> RunOutcome:
    """Run the analysis, record its selection and create the children, resuming where it stopped.

    The outcome is `RUNNING` when the children exist and the Question's work can go on, and
    otherwise the state that stopped it, such as `WAITING_FOR_SELECTION` for a fired trigger.
    """
    if not is_automatic(ticket, deps.config.policy):
        raise ValueError(f"#{ticket.number} is not an M Question")
    run_id = Engine(deps)._find_or_create_run(ticket.number).id
    live = await deps.github.get_ticket(ticket.number)
    if not selected_solution(live):
        outcome = await analyse(deps, live, run_id=run_id)
        if outcome.state != RunState.RUNNING:
            return outcome
        live = await deps.github.get_ticket(ticket.number)
    return await _create_children(deps, live, run_id)


async def _create_children(deps: EngineDeps, question: Ticket, run_id: int) -> RunOutcome:
    client = deps.github
    if not isinstance(client, GitHubClient):
        return _blocked(run_id, "Creating children needs the GitHub client")
    if not selected_solution(question):
        return _blocked(run_id, "The Question has no selected solution")
    for field, value in (("milestone", question.milestone), ("assignee", question.assignees)):
        if not value:
            return _blocked(run_id, f"Question is missing {field}; use fugue children")
    proposals = ProposalStore(deps.state_dir)
    saved = proposals.load(client.repo, question.number)
    if saved is not None and saved.get("numbers"):
        return RunOutcome(run_id, RunState.RUNNING, notes=("Children already created",))
    if saved is None and await client.children(question.number):
        # Children made by hand or by an earlier tool: proposing more would duplicate them.
        return RunOutcome(run_id, RunState.RUNNING, notes=("Children already exist",))
    scope_dir = proposals.path(client.repo, question.number).with_suffix("")
    try:
        if saved is None:
            saved = await _propose(deps, question, run_id, client, scope_dir)
            proposals.save(client.repo, question.number, saved)
        planned = validate(
            saved, question, saved["milestone"], saved["assignee"], scope_dir, deps.config.policy
        )
    except (ValueError, OSError, TypeError, KeyError) as error:
        return _blocked(run_id, f"Child proposal: {error}", "next: fugue question to edit it")
    saved["locked"] = True
    proposals.save(client.repo, question.number, saved)
    saved["numbers"] = await apply(planned, client, deps.store)
    proposals.save(client.repo, question.number, saved)
    created = ", ".join(f"#{number}" for number in saved["numbers"].values())
    return RunOutcome(run_id, RunState.RUNNING, notes=(f"Created children {created}",))


async def _propose(
    deps: EngineDeps, question: Ticket, run_id: int, client: GitHubClient, scope_dir: Path
) -> dict[str, Any]:
    role = deps.config.roles[Role.ANALYST]
    adapter = role_adapter(deps, run_id, Role.ANALYST, role)
    if Capability.READ_ONLY_MODE not in adapter.capabilities:
        raise ValueError("Analyst adapter lacks read-only mode")
    url = scope_url(question)
    if url is None:
        raise ValueError("Selected solution has no scope attachment")
    scope_dir.mkdir(parents=True, exist_ok=True)
    scope = await client.download_attachment(url, scope_dir / "selected.md")
    request = build_request(
        question,
        question.body_sections.get("Analysis", ""),
        scope.read_text(encoding="utf-8"),
        [],
        config=deps.config,
        cwd=deps.effective_code_root,
        size=question.size,
    )
    assert request.access == AccessMode.READ_ONLY
    choice = model_choice(deps, run_id, Role.ANALYST, question.size)
    reply = await adapter.run(request)
    if reply.usage is not None:
        deps.store.record_usage(
            run_id,
            Role.ANALYST,
            reply.provider,
            reply.authentication_mode,
            **asdict(reply.usage),
            **usage_choice(choice, reply.provider),
        )
    if not reply.ok or reply.cancelled or reply.timed_out or reply.unsupported:
        raise ValueError(reply.error or "Child proposal analyst call failed")
    raw = dict(reply.structured_output or json.loads(reply.final_text))
    if not isinstance(raw.get("children"), list) or not all(
        isinstance(child, dict) for child in raw["children"]
    ):
        raise ValueError("Analyst returned an unusable child proposal")
    return {
        "children": raw["children"],
        "locked": False,
        "milestone": question.milestone,
        "assignee": question.assignees[0],
    }
