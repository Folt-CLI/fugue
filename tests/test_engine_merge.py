"""Exact-head approvals, merge guards and restartable closure."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_engine_review import (
    LINES,
    FakeReviewGitHub,
    Harness,
    git_command,
    review,
    write,
)
from test_engine_review import (
    harness as review_harness,
)
from test_github import PR, REPO, Fake, fake, response

from fugue.engine.pipeline import Engine
from fugue.github import CheckResult, PullRequest
from fugue.github.client import PrState
from fugue.gitops import Git
from fugue.models import Disposition, Size, Status, Ticket
from fugue.workflow import RunState

# Expose the imported fixture to pytest.
assert review_harness
assert fake


class MergeGitHub(FakeReviewGitHub):
    def __init__(self, ticket: Ticket, worktree: Path) -> None:
        super().__init__(ticket)
        self.worktree = worktree
        self.live_head = ""
        self.state = "OPEN"
        self.merge_sha: str | None = None
        self.events: list[str] = []
        self.comment_bodies: list[str] = []
        self.merge_error = False
        self.cleanup_error = False
        self.green = True
        self.child_tickets: list[Ticket] = []

    async def pr_head_sha(self, pr: PullRequest) -> str:
        return self.live_head or await Git(self.worktree).head_sha()

    async def pr_state(self, pr: PullRequest) -> PrState:
        self.events.append("state:" + self.state)
        return PrState(self.state, await self.pr_head_sha(pr), self.merge_sha)

    async def checks(self, pr: PullRequest) -> list[CheckResult]:
        return [CheckResult("CI", "pass" if self.green else "fail", "")]

    async def merge(self, pr: PullRequest, expected_head_sha: str) -> None:
        self.events.append("merge")
        assert expected_head_sha == await self.pr_head_sha(pr)
        if self.merge_error:
            raise RuntimeError("merge failed")
        self.live_head = expected_head_sha
        self.state = "MERGED"
        self.merge_sha = "merge-sha"

    async def set_disposition(self, number: int, disposition: Disposition) -> None:
        assert disposition == Disposition.MERGED
        assert self.worktree.exists()
        self.events.append("disposition")

    async def set_status(self, number: int, status: Status) -> None:
        if status == Status.DONE:
            self.events.append("status")
        await super().set_status(number, status)

    async def comment(self, number: int, body: str) -> None:
        self.events.append("comment")
        self.comment_bodies.append(body)

    async def comments(self, number: int) -> list[str]:
        assert number == self.ticket.number
        return list(self.comment_bodies)

    async def children(self, number: int) -> list[Ticket]:
        return self.child_tickets

    async def delete_remote_branch(self, branch: str, repo_root: Path) -> None:
        assert not self.worktree.exists()
        if self.cleanup_error:
            raise RuntimeError("cleanup interrupted")
        self.events.append("delete")
        await Git(repo_root)._run("push", "--delete", "--", "origin", f"refs/heads/{branch}")


@pytest.fixture
def ready(review_harness: Harness) -> Iterator[tuple[Harness, MergeGitHub, int]]:
    h = review_harness
    github = MergeGitHub(h.github.ticket, h.worktree)
    h.github = github
    h.deps.github = github
    h.script(write(LINES), review("No findings."))
    run_id = h.start()
    h.review(run_id)
    github.events.clear()
    github.comment_bodies.clear()
    yield h, github, run_id


def request(h: Harness, run_id: int) -> None:
    outcome = asyncio.run(Engine(h.deps).request_approval(run_id))
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL, outcome.problems


def test_happy_merge_closes_and_cleans_after_confirmation(
    ready: tuple[Harness, MergeGitHub, int],
) -> None:
    h, github, run_id = ready
    request(h, run_id)
    approval = Engine(h.deps).approve(run_id, "developer", "Ready to merge")
    result = asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert result.state == RunState.COMPLETED
    assert github.events == [
        "state:OPEN",
        "merge",
        "state:MERGED",
        "disposition",
        "status",
        "comment",
        "delete",
    ]
    assert not h.worktree.exists()
    assert f"Approval #{approval.id}" in github.comment_bodies[0]
    assert "merge-sha" in github.comment_bodies[0] and approval.head_sha in github.comment_bodies[0]
    assert len(github.comment_bodies) == 1
    assert f"<!-- fugue:closure run={run_id} step=comment -->" in github.comment_bodies[0]
    assert github.ticket.status == Status.DONE
    asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert len(github.comment_bodies) == 1 and github.events.count("delete") == 1


def test_new_commit_rejects_old_approval(ready: tuple[Harness, MergeGitHub, int]) -> None:
    h, github, run_id = ready
    request(h, run_id)
    Engine(h.deps).approve(run_id, "developer", "ready")
    github.live_head = "new-head"
    with pytest.raises(ValueError, match=r"gate|review|head"):
        Engine(h.deps).approve(run_id, "developer", "ready")
    outcome = asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert outcome.state == RunState.BLOCKED
    assert "Merge approval must match the current head SHA" in outcome.problems
    assert h.worktree.exists() and "merge" not in github.events


@pytest.mark.parametrize("guard", ["gate", "ci", "review", "threads", "approval"])
def test_each_guard_prevents_merge(
    ready: tuple[Harness, MergeGitHub, int],
    guard: str,
) -> None:
    h, github, run_id = ready
    request(h, run_id)
    if guard != "approval":
        Engine(h.deps).approve(run_id, "developer", "ready")
    if guard == "gate":
        h.deps.store._connection.execute("DELETE FROM operations WHERE kind = 'merge_gate'")
    elif guard == "ci":
        github.green = False
    elif guard == "review":
        h.deps.store._connection.execute("DELETE FROM operations WHERE kind = 'review'")
    elif guard == "threads":
        github.unresolved_extra = 1
    result = asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert result.state == RunState.BLOCKED and result.problems
    assert h.worktree.exists() and "merge" not in github.events


def test_merge_failure_keeps_worktree(ready: tuple[Harness, MergeGitHub, int]) -> None:
    h, github, run_id = ready
    request(h, run_id)
    Engine(h.deps).approve(run_id, "developer", "ready")
    github.merge_error = True
    with pytest.raises(RuntimeError, match="merge failed"):
        asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert h.worktree.exists() and "disposition" not in github.events


def test_restart_finishes_closure_once(ready: tuple[Harness, MergeGitHub, int]) -> None:
    h, github, run_id = ready
    request(h, run_id)
    Engine(h.deps).approve(run_id, "developer", "ready")
    github.cleanup_error = True
    with pytest.raises(RuntimeError, match="cleanup interrupted"):
        asyncio.run(Engine(h.deps).merge_and_close(run_id))
    github.cleanup_error = False
    result = asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert result.state == RunState.COMPLETED
    assert github.events.count("merge") == github.events.count("disposition") == 1
    assert github.events.count("status") == len(github.comment_bodies) == 1


def test_restart_after_closure_comment_response_is_lost(
    ready: tuple[Harness, MergeGitHub, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, github, run_id = ready
    request(h, run_id)
    Engine(h.deps).approve(run_id, "developer", "ready")
    original = github.comment

    async def lost_response(number: int, body: str) -> None:
        await original(number, body)
        raise ConnectionError("comment response lost")

    monkeypatch.setattr(github, "comment", lost_response)
    with pytest.raises(ConnectionError, match="comment response lost"):
        asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert github.state == "MERGED" and len(github.comment_bodies) == 1
    assert h.worktree.exists()
    monkeypatch.setattr(github, "comment", original)
    result = asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert result.state == RunState.COMPLETED
    assert github.events.count("merge") == github.events.count("comment") == 1
    assert len(github.comment_bodies) == 1
    assert not h.worktree.exists()
    asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert len(github.comment_bodies) == 1


def test_client_reads_all_ticket_comment_pages(fake: Fake) -> None:
    args = ["api", f"repos/{REPO}/issues/40/comments", "--paginate", "--slurp"]
    client, calls = fake([(args, response([[{"body": "First"}], [{"body": "Closure marker"}]]))])
    assert asyncio.run(client.comments(40)) == ["First", "Closure marker"]
    assert calls()[0]["argv"] == args


def test_unclosed_child_blocks_closure(ready: tuple[Harness, MergeGitHub, int]) -> None:
    h, github, run_id = ready
    request(h, run_id)
    Engine(h.deps).approve(run_id, "developer", "ready")
    github.child_tickets = [github.ticket.model_copy(update={"number": 80})]
    result = asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert result.state == RunState.BLOCKED
    assert "Child #80" in result.problems[0]
    assert h.worktree.exists() and "disposition" not in github.events
    github.child_tickets = []
    assert asyncio.run(Engine(h.deps).merge_and_close(run_id)).state == RunState.COMPLETED


def test_size_without_review_still_needs_approval(ready: tuple[Harness, MergeGitHub, int]) -> None:
    h, github, run_id = ready
    github.ticket = github.ticket.model_copy(update={"size": Size.XS})
    h.deps.store._connection.execute("DELETE FROM operations WHERE kind = 'review'")
    request(h, run_id)
    assert h.deps.config.policy.autonomy_profile == "human"
    result = asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert result.state == RunState.BLOCKED and "merge" not in github.events
    Engine(h.deps).approve(run_id, "developer", "ready")
    assert asyncio.run(Engine(h.deps).merge_and_close(run_id)).state == RunState.COMPLETED


def test_approval_rejects_dirty_worktree(ready: tuple[Harness, MergeGitHub, int]) -> None:
    h, _, run_id = ready
    request(h, run_id)
    (h.worktree / "allowed.txt").write_text("unverified")
    with pytest.raises(ValueError, match="clean"):
        Engine(h.deps).approve(run_id, "developer", "ready")


def test_new_local_commit_needs_new_gate_and_review(
    ready: tuple[Harness, MergeGitHub, int],
) -> None:
    h, _, run_id = ready
    request(h, run_id)
    (h.worktree / "allowed.txt").write_text("new commit")
    asyncio.run(git_command(h.worktree, "commit", "-am", "new commit"))
    with pytest.raises(ValueError, match=r"gate|review"):
        Engine(h.deps).approve(run_id, "developer", "ready")


def test_restart_after_merge_response_is_lost(
    ready: tuple[Harness, MergeGitHub, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, github, run_id = ready
    request(h, run_id)
    Engine(h.deps).approve(run_id, "developer", "ready")
    original = github.merge

    async def lost_response(pr: PullRequest, head: str) -> None:
        await original(pr, head)
        raise RuntimeError("response lost")

    monkeypatch.setattr(github, "merge", lost_response)
    with pytest.raises(RuntimeError, match="response lost"):
        asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert h.worktree.exists() and github.state == "MERGED"
    assert asyncio.run(Engine(h.deps).merge_and_close(run_id)).state == RunState.COMPLETED
    assert github.events.count("merge") == 1 and len(github.comment_bodies) == 1


def test_cleanup_requires_confirmed_merge(
    ready: tuple[Harness, MergeGitHub, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, github, run_id = ready
    request(h, run_id)
    Engine(h.deps).approve(run_id, "developer", "ready")

    async def closed(pr: PullRequest) -> PrState:
        return PrState(
            "OPEN" if github.state == "OPEN" else "CLOSED", await Git(h.worktree).head_sha()
        )

    monkeypatch.setattr(github, "pr_state", closed)
    outcome = asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert outcome.state == RunState.BLOCKED
    assert h.worktree.exists() and "disposition" not in github.events


def test_failed_gate_request_invalidates_previous_gate_evidence(
    ready: tuple[Harness, MergeGitHub, int],
) -> None:
    h, _, run_id = ready
    request(h, run_id)
    h.deps.config.policy.gate_commands = [["false"]]
    result = asyncio.run(Engine(h.deps).request_approval(run_id))
    assert result.state == RunState.BLOCKED and result.problems == ("The gate must pass",)
    with pytest.raises(ValueError, match="gate"):
        Engine(h.deps).approve(run_id, "developer", "ready")


@pytest.mark.parametrize(
    ("state", "merge_commit", "expected"),
    [("OPEN", None, None), ("MERGED", {"oid": "merged-sha"}, "merged-sha")],
)
def test_client_reads_pr_state(
    fake: Fake,
    state: str,
    merge_commit: dict[str, str] | None,
    expected: str | None,
) -> None:
    args = ["pr", "view", str(PR.number), "--repo", REPO, "--json", "state,headRefOid,mergeCommit"]
    client, calls = fake(
        [
            (
                args,
                response(
                    {
                        "state": state,
                        "headRefOid": "head",
                        "mergeCommit": merge_commit,
                    }
                ),
            )
        ]
    )
    assert asyncio.run(client.pr_state(PR)) == PrState(state, "head", expected)
    assert calls()[0]["argv"] == args


def test_client_deletes_only_explicit_remote_ref(
    fake: Fake,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = fake([])
    commands: list[tuple[str, ...]] = []

    async def capture(self: Git, *args: str, stdin: str | None = None) -> str:
        assert self.repo == tmp_path
        commands.append(args)
        return "sha refs/heads/feature/example" if args[0] == "ls-remote" else ""

    monkeypatch.setattr(Git, "_run", capture)
    asyncio.run(client.delete_remote_branch("feature/example", tmp_path))
    assert commands[-1] == ("push", "--delete", "--", "origin", "refs/heads/feature/example")
    assert calls() == []
