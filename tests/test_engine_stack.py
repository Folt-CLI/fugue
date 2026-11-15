"""Delivery of additional stack layers and pull request readiness."""

import asyncio
import json
from typing import Literal

import pytest
from test_engine_to_pr import Harness, git_command, result
from test_engine_to_pr import harness as harness
from test_stack import FakeStack
from test_stack import stacked as stacked

from fugue.agents import AgentRequest, ScriptedAdapter, ScriptStep
from fugue.engine import Engine
from fugue.engine.facts import branch_name
from fugue.github import PullRequest
from fugue.models import Status, Ticket
from fugue.workflow import RunState


@pytest.mark.parametrize("interruption", ["none", "before_tracking", "after_tracking"])
def test_third_layer_joins_one_stack_and_each_delivered_pr_is_ready(
    stacked: tuple[Harness, FakeStack, str],
    monkeypatch: pytest.MonkeyPatch,
    interruption: Literal["none", "before_tracking", "after_tracking"],
) -> None:
    h, fake, bottom = stacked
    assert h.run().state == RunState.WAITING_FOR_CI
    middle_ticket = h.github.ticket
    middle = branch_name(middle_ticket)
    middle_pr = h.github.pr
    state_path = h.deps.repo_root / ".git" / "gh-stack"
    original_state = json.loads(state_path.read_text())
    h.github.ticket = middle_ticket.model_copy(
        update={
            "number": 34,
            "title": "Third layer",
            "blocked_by": [33],
            "status": Status.TO_DO,
            "body_sections": {
                **middle_ticket.body_sections,
                "Solution": "[34-solution-1.md](https://github.com/user-attachments/files/scope)",
            },
        }
    )
    h.github.pr = None

    async def get_ticket(number: int) -> Ticket:
        return middle_ticket if number == 33 else h.github.ticket

    async def find_pr(branch: str) -> PullRequest | None:
        return middle_pr if branch == middle else h.github.pr

    def implement(request: AgentRequest) -> None:
        (request.cwd / "allowed.txt").write_text("third layer")

    adapter = ScriptedAdapter([ScriptStep(result=result(), side_effect=implement)])
    h.deps.registry.register("codex", lambda config: adapter)
    monkeypatch.setattr(h.github, "get_ticket", get_ticket)
    monkeypatch.setattr(h.github, "find_pr", find_pr)
    if interruption != "none":
        original_track = fake.track

        async def interrupted_track(lower: str, branch: str) -> None:
            if interruption == "after_tracking":
                await original_track(lower, branch)
            raise RuntimeError("interrupted local tracking")

        with monkeypatch.context() as patch:
            patch.setattr(fake, "track", interrupted_track)
            with pytest.raises(RuntimeError, match="interrupted local tracking"):
                asyncio.run(Engine(h.deps).run_to_pr(34))
        assert fake.submits == 1
        assert fake.ready == [middle]
        assert len(fake.metadata) == 1
    outcome = asyncio.run(Engine(h.deps).run_to_pr(34))
    assert outcome.state == RunState.WAITING_FOR_CI, outcome.problems
    upper = branch_name(h.github.ticket)
    assert fake.branches == [bottom, middle, upper]
    assert fake.initializations == 1
    assert fake.links == [("168", upper)] * (2 if interruption == "before_tracking" else 1)
    assert fake.ready == [middle, upper]
    assert fake.submits == 2
    state = json.loads(state_path.read_text())
    assert len(state["stacks"]) == 1
    assert state["stacks"][0]["branches"][:2] == original_state["stacks"][0]["branches"]
    tracked_upper = state["stacks"][0]["branches"][2]
    assert tracked_upper == {
        "branch": upper,
        "head": outcome.head_sha,
        "base": asyncio.run(git_command(h.deps.repo_root, "rev-parse", middle)),
    }
    assert asyncio.run(git_command(h.deps.repo_root, "branch", "--show-current")) == "develop"
    assert asyncio.run(fake.view()) == " ".join([bottom, middle, upper])


def test_ready_failure_restarts_after_metadata_without_resubmit(
    stacked: tuple[Harness, FakeStack, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    h, fake, _ = stacked

    async def fail(repo: str, branch: str) -> None:
        raise RuntimeError("interrupted readiness")

    with monkeypatch.context() as patch:
        patch.setattr(fake, "ready_pr", fail)
        with pytest.raises(RuntimeError, match="interrupted readiness"):
            h.run()
    assert len(fake.metadata) == 1
    assert fake.ready == []
    assert h.run().state == RunState.WAITING_FOR_CI
    assert h.run().state == RunState.WAITING_FOR_CI
    assert fake.submits == 1
    assert len(fake.metadata) == 1
    assert fake.ready == [branch_name(h.github.ticket)]
