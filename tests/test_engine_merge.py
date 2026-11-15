"""Exact-head approvals, merge guards and restartable closure."""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

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

from fugue.engine.facts import branch_name
from fugue.engine.pipeline import Engine, RunOutcome
from fugue.github import CheckResult, GitHubError, PullRequest
from fugue.github.client import PrState
from fugue.gitops import Git
from fugue.models import Disposition, Size, Status, Ticket
from fugue.workflow import RunState

# Expose the imported fixture to pytest.
assert review_harness
assert fake


class MergeGitHub(FakeReviewGitHub):
    def __init__(self, ticket: Ticket, worktree: Path, origin: Path) -> None:
        super().__init__(ticket)
        self.worktree = worktree
        self.origin = origin
        self.live_head = ""
        self.state = "OPEN"
        self.merge_sha: str | None = None
        self.events: list[str] = []
        self.comment_bodies: list[str] = []
        self.merge_error = False
        self.cleanup_error = False
        self.green = True
        self.child_tickets: list[Ticket] = []
        self.dependent_prs: list[dict[str, Any]] = []
        self.base_queries: list[str] = []

    async def _paginated(self, endpoint: str, *args: str) -> list[dict[str, Any]]:
        branch = quote(branch_name(self.ticket), safe="")
        assert endpoint == f"repos/{self.repo}/pulls?state=open&base={branch}&per_page=100"
        self.base_queries.append(endpoint)
        return self.dependent_prs

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
        base = (await git_command(self.origin, "rev-parse", "refs/heads/develop")).strip()
        tree = (
            await git_command(self.origin, "rev-parse", f"{expected_head_sha}^{{tree}}")
        ).strip()
        self.merge_sha = (
            await git_command(
                self.origin,
                "-c",
                "user.name=Test Developer",
                "-c",
                "user.email=test@example.test",
                "commit-tree",
                tree,
                "-p",
                base,
                "-m",
                "Squash merge",
            )
        ).strip()
        await git_command(self.origin, "update-ref", "refs/heads/develop", self.merge_sha, base)
        self.live_head = expected_head_sha
        self.state = "MERGED"

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
    github = MergeGitHub(h.github.ticket, h.worktree, h.origin)
    h.github = github
    h.deps.github = github
    h.script(write(LINES), review("No findings."))
    run_id = h.start()
    (h.worktree / "allowed.txt").write_text(LINES + "second commit\n")
    asyncio.run(git_command(h.worktree, "commit", "-am", "Second change"))
    asyncio.run(git_command(h.worktree, "push", "origin", "HEAD"))
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
    primary_head = asyncio.run(Git(h.deps.repo_root).head_sha())
    branch = branch_name(github.ticket)
    assert (
        asyncio.run(git_command(h.worktree, "rev-list", "--count", f"{primary_head}..HEAD")).strip()
        == "2"
    )
    assert (
        asyncio.run(
            git_command(h.worktree, "for-each-ref", "--format=%(upstream)", f"refs/heads/{branch}")
        )
        == ""
    )
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
    branches = asyncio.run(git_command(h.deps.repo_root, "branch", "--list"))
    assert branch_name(github.ticket) not in branches
    assert result.notes == ()
    assert len(github.base_queries) == 1
    assert f"Approval #{approval.id}" in github.comment_bodies[0]
    assert github.merge_sha is not None and github.merge_sha in github.comment_bodies[0]
    assert approval.head_sha in github.comment_bodies[0]
    assert asyncio.run(Git(h.deps.repo_root).head_sha()) == primary_head
    assert (
        asyncio.run(
            git_command(h.origin, "rev-list", "--count", f"{primary_head}..develop")
        ).strip()
        == "1"
    )
    assert (
        asyncio.run(git_command(h.origin, "rev-list", f"{primary_head}..develop")).strip()
        != approval.head_sha
    )
    assert len(github.comment_bodies) == 1
    assert f"<!-- fugue:closure run={run_id} step=comment -->" in github.comment_bodies[0]
    assert github.ticket.status == Status.DONE
    asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert len(github.comment_bodies) == 1 and github.events.count("delete") == 1
    assert len(github.base_queries) == 1


def local_branch_result(h: Harness, run_id: int) -> dict[str, Any]:
    row = h.deps.store._connection.execute(
        "SELECT status, result_json FROM operations "
        "WHERE run_id = ? AND kind = 'closure' AND key = 'local_branch'",
        (run_id,),
    ).fetchone()
    assert row is not None and row[0] == "done"
    result: dict[str, Any] = json.loads(row[1])
    return result


def test_unmerged_local_branch_warns_without_changing_merge_result(
    ready: tuple[Harness, MergeGitHub, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, github, run_id = ready
    reports: list[str] = []
    h.deps.progress = reports.append
    request(h, run_id)
    approval = Engine(h.deps).approve(run_id, "developer", "ready")
    original = github.merge
    advanced_head = ""

    async def merge_then_advance(pr: PullRequest, head: str) -> None:
        nonlocal advanced_head
        await original(pr, head)
        (h.worktree / "allowed.txt").write_text("Unmerged change after approval\n")
        await git_command(h.worktree, "commit", "-am", "Unmerged change")
        advanced_head = await Git(h.worktree).head_sha()

    monkeypatch.setattr(github, "merge", merge_then_advance)
    result = asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert result.state == RunState.COMPLETED and result.ci_green
    assert result.head_sha == approval.head_sha and github.state == "MERGED"
    assert github.merge_sha is not None and github.ticket.status == Status.DONE
    assert not h.worktree.exists()
    assert asyncio.run(Git(h.deps.repo_root).head_sha(branch_name(github.ticket))) == advanced_head
    assert len(result.notes) == 1 and result.notes[0].startswith("Warning:")
    assert "expected" in result.notes[0]
    assert result.notes[0] in reports
    assert local_branch_result(h, run_id)["deleted"] is False
    assert asyncio.run(Engine(h.deps).merge_and_close(run_id)).notes == result.notes


def test_local_branch_used_as_open_pr_base_is_kept(
    ready: tuple[Harness, MergeGitHub, int],
) -> None:
    h, github, run_id = ready
    github.dependent_prs = [{"number": 99}]
    request(h, run_id)
    approval = Engine(h.deps).approve(run_id, "developer", "ready")
    result = asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert result.state == RunState.COMPLETED
    assert (
        asyncio.run(Git(h.deps.repo_root).head_sha(branch_name(github.ticket))) == approval.head_sha
    )
    assert "base of another open pull request" in result.notes[0]
    assert local_branch_result(h, run_id)["deleted"] is False


def test_failed_open_pr_check_keeps_local_branch_and_warns(
    ready: tuple[Harness, MergeGitHub, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, github, run_id = ready
    request(h, run_id)
    approval = Engine(h.deps).approve(run_id, "developer", "ready")

    async def unavailable(endpoint: str, *args: str) -> list[dict[str, Any]]:
        raise GitHubError(("api", endpoint), "Cannot read pull requests")

    monkeypatch.setattr(github, "_paginated", unavailable)
    result = asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert result.state == RunState.COMPLETED
    assert (
        asyncio.run(Git(h.deps.repo_root).head_sha(branch_name(github.ticket))) == approval.head_sha
    )
    assert "Cannot read pull requests" in result.notes[0]
    assert local_branch_result(h, run_id)["deleted"] is False


def test_restart_after_local_branch_deletion_response_is_lost(
    ready: tuple[Harness, MergeGitHub, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, github, run_id = ready
    request(h, run_id)
    Engine(h.deps).approve(run_id, "developer", "ready")
    original = Git.delete_local_branch

    async def lost_response(
        git: Git, branch: str, *, merged_head: str | None = None
    ) -> tuple[bool, str | None]:
        assert await original(git, branch, merged_head=merged_head) == (True, None)
        raise RuntimeError("local deletion response lost")

    monkeypatch.setattr(Git, "delete_local_branch", lost_response)
    with pytest.raises(RuntimeError, match="local deletion response lost"):
        asyncio.run(Engine(h.deps).merge_and_close(run_id))
    monkeypatch.setattr(Git, "delete_local_branch", original)
    result = asyncio.run(Engine(h.deps).merge_and_close(run_id))
    assert result.state == RunState.COMPLETED and result.notes == ()
    assert local_branch_result(h, run_id)["reason"] == "Branch is already absent"
    assert github.events.count("merge") == 1


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


class StackMergeFixture:
    def __init__(
        self,
        h: Harness,
        parent_id: int,
        runs: list[int],
        tickets: list[Ticket],
        paths: list[Path],
        heads: list[str],
    ) -> None:
        self.h = h
        self.parent_id = parent_id
        self.runs = runs
        self.tickets = tickets
        self.paths = paths
        self.heads = heads
        self.states = ["OPEN", "OPEN"]
        self.prs = [
            PullRequest(201, "https://example.test/201"),
            PullRequest(202, "https://example.test/202"),
        ]
        self.branches = [branch_name(ticket) for ticket in tickets]
        self.merges = 0
        self.lost_response = False
        self.fail_merge = False
        self.closed: list[int] = []
        self.comment_bodies: dict[int, list[str]] = {ticket.number: [] for ticket in tickets}

    async def tracked_branches(self, branch: str) -> list[str]:
        return self.branches

    async def merge(self, number: int) -> str:
        assert number == self.prs[0].number
        self.merges += 1
        if self.fail_merge:
            raise GitHubError(("stack", "merge"), "draft or conflict")
        self.states = ["MERGED", "MERGED"]
        if self.lost_response:
            raise ConnectionError("stack response lost")
        return "merged"

    def request(self) -> None:
        result = asyncio.run(Engine(self.h.deps).request_approval(self.parent_id, parent=True))
        assert result.state == RunState.WAITING_FOR_MERGE_APPROVAL, result.problems

    def approve(self) -> None:
        Engine(self.h.deps).approve(self.parent_id, "developer", "Whole stack ready", parent=True)

    def finish(self) -> RunOutcome:
        return asyncio.run(Engine(self.h.deps).merge_and_close(self.parent_id, parent=True))


@pytest.fixture
def stack_ready(
    ready: tuple[Harness, MergeGitHub, int], monkeypatch: pytest.MonkeyPatch
) -> StackMergeFixture:
    from typing import cast

    from fugue.github.stack import StackClient

    h, github, run_id = ready
    store = h.deps.store
    lower = github.ticket.model_copy(update={"size": Size.XS})
    upper = lower.model_copy(update={"number": lower.number + 1, "title": "Upper layer"})
    path = h.worktree.parent / "upper-layer"
    asyncio.run(
        git_command(
            h.deps.repo_root,
            "worktree",
            "add",
            "-b",
            branch_name(upper),
            str(path),
            branch_name(lower),
        )
    )
    upper_run = store.create_run(upper.number, github.repo)
    identity = store.begin_operation(upper_run.id, "identity", "identity", {})
    store.complete_operation(identity.id, {"branch": branch_name(upper), "path": str(path)})
    parent = store.create_parent_run(170, github.repo)
    runs = [run_id, upper_run.id]
    for position, (ticket, child_run) in enumerate(zip([lower, upper], runs, strict=True)):
        store.add_work(parent.id, ticket.number, position)
        store.set_work_run(parent.id, ticket.number, child_run)
    heads = [asyncio.run(Git(p).head_sha()) for p in [h.worktree, path]]
    fixture = StackMergeFixture(h, parent.id, runs, [lower, upper], [h.worktree, path], heads)

    async def get_ticket(number: int) -> Ticket:
        return next(ticket for ticket in fixture.tickets if ticket.number == number)

    async def find_pr(branch: str) -> PullRequest:
        return fixture.prs[fixture.branches.index(branch)]

    async def head(pr: PullRequest) -> str:
        return fixture.heads[fixture.prs.index(pr)]

    async def state(pr: PullRequest) -> PrState:
        index = fixture.prs.index(pr)
        return PrState(
            fixture.states[index],
            fixture.heads[index],
            f"merge-{index}" if fixture.states[index] == "MERGED" else None,
        )

    async def disposition(number: int, value: Disposition) -> None:
        assert all(state == "MERGED" for state in fixture.states)
        fixture.closed.append(number)

    async def status(number: int, value: Status) -> None:
        pass

    async def comment(number: int, body: str) -> None:
        fixture.comment_bodies[number].append(body)

    async def comments(number: int) -> list[str]:
        return fixture.comment_bodies[number]

    async def remote(branch: str, repo_root: Path) -> None:
        pass

    async def paginated(endpoint: str, *args: str) -> list[dict[str, Any]]:
        return []

    for name, method in {
        "get_ticket": get_ticket,
        "find_pr": find_pr,
        "pr_head_sha": head,
        "pr_state": state,
        "set_disposition": disposition,
        "set_status": status,
        "comment": comment,
        "comments": comments,
        "delete_remote_branch": remote,
        "_paginated": paginated,
    }.items():
        monkeypatch.setattr(github, name, method)
    h.deps.stack_factory = lambda _: cast(StackClient, fixture)
    return fixture


def test_stack_approval_merges_once_and_closes_every_work(stack_ready: StackMergeFixture) -> None:
    s = stack_ready
    s.request()
    s.approve()
    assert s.finish().state == RunState.COMPLETED
    assert s.merges == 1
    assert s.closed == [ticket.number for ticket in s.tickets]
    assert all(not path.exists() for path in s.paths)
    assert all(s.h.deps.store.get_run(run).state == RunState.COMPLETED for run in s.runs)
    assert s.finish().state == RunState.COMPLETED
    assert s.merges == 1 and len(s.closed) == 2
    assert all(len(bodies) == 1 for bodies in s.comment_bodies.values())


@pytest.mark.parametrize("layer", [0, 1])
def test_changed_stack_layer_lapses_entire_approval(
    stack_ready: StackMergeFixture, layer: int
) -> None:
    s = stack_ready
    s.request()
    s.approve()
    old = s.heads[layer]
    s.heads[layer] = "new-head"
    assert s.finish().state == RunState.BLOCKED
    assert s.merges == 0 and s.closed == []
    s.heads[layer] = old
    assert s.finish().state == RunState.BLOCKED
    assert s.merges == 0


def test_stack_reconciles_lost_merge_response(stack_ready: StackMergeFixture) -> None:
    s = stack_ready
    s.request()
    s.approve()
    s.lost_response = True
    with pytest.raises(ConnectionError, match="stack response lost"):
        s.finish()
    assert s.finish().state == RunState.COMPLETED
    assert s.merges == 1 and len(s.closed) == 2


def test_stack_reconciles_interrupted_closure(
    stack_ready: StackMergeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = stack_ready
    s.request()
    s.approve()
    github = s.h.deps.github
    original = github.comment

    async def lost_response(number: int, body: str) -> None:
        await original(number, body)
        if number == s.tickets[1].number:
            raise ConnectionError("closure response lost")

    monkeypatch.setattr(github, "comment", lost_response)
    with pytest.raises(ConnectionError, match="closure response lost"):
        s.finish()
    assert not s.paths[0].exists() and s.paths[1].exists()
    monkeypatch.setattr(github, "comment", original)
    assert s.finish().state == RunState.COMPLETED
    assert s.merges == 1 and len(s.closed) == 2
    assert all(len(bodies) == 1 for bodies in s.comment_bodies.values())


def test_stack_merge_failure_closes_nothing(stack_ready: StackMergeFixture) -> None:
    s = stack_ready
    s.request()
    s.approve()
    s.fail_merge = True
    with pytest.raises(GitHubError, match="draft or conflict"):
        s.finish()
    assert s.closed == [] and all(path.exists() for path in s.paths)


def test_stack_record_requires_current_request(stack_ready: StackMergeFixture) -> None:
    s = stack_ready
    with pytest.raises(ValueError):
        s.approve()
    assert s.finish().state == RunState.BLOCKED and s.merges == 0


@pytest.mark.parametrize("guard", ["ci", "threads", "gate", "review"])
def test_each_layer_must_pass_guards(
    stack_ready: StackMergeFixture, monkeypatch: pytest.MonkeyPatch, guard: str
) -> None:
    s = stack_ready
    s.request()
    s.approve()
    github = s.h.deps.github
    if guard == "ci":

        async def checks(pr: PullRequest) -> list[CheckResult]:
            return [CheckResult("CI", "fail" if pr == s.prs[1] else "pass", "")]

        monkeypatch.setattr(github, "checks", checks)
    elif guard == "threads":

        async def unresolved(pr: PullRequest) -> int:
            return int(pr == s.prs[1])

        monkeypatch.setattr(github, "unresolved_thread_count", unresolved)
    elif guard == "gate":
        s.h.deps.store._connection.execute(
            "DELETE FROM operations WHERE run_id = ? AND kind = 'merge_gate'", (s.runs[1],)
        )
    else:
        s.tickets[1] = s.tickets[1].model_copy(update={"size": Size.S})
    assert s.finish().state == RunState.BLOCKED
    assert s.merges == 0 and s.closed == []


def test_unapproved_extra_layer_blocks_atomic_merge(
    stack_ready: StackMergeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = stack_ready
    s.request()
    s.approve()

    async def branches(branch: str) -> list[str]:
        return [*s.branches, "unapproved-layer"]

    monkeypatch.setattr(s, "tracked_branches", branches)
    assert s.finish().state == RunState.BLOCKED
    assert s.merges == 0


def test_head_change_during_final_guards_blocks_merge(
    stack_ready: StackMergeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = stack_ready
    s.request()
    s.approve()

    async def checks(pr: PullRequest) -> list[CheckResult]:
        if pr == s.prs[1]:
            s.heads[0] = "changed-during-guards"
        return [CheckResult("CI", "pass", "")]

    monkeypatch.setattr(s.h.deps.github, "checks", checks)
    assert s.finish().state == RunState.BLOCKED
    assert s.merges == 0


def test_unconfirmed_layer_prevents_every_closure(stack_ready: StackMergeFixture) -> None:
    s = stack_ready
    s.request()
    s.approve()
    s.states = ["MERGED", "CLOSED"]
    assert s.finish().state == RunState.BLOCKED
    assert s.merges == 0 and s.closed == []


def test_stack_holds_every_ticket_lock_and_releases_after_failure(
    stack_ready: StackMergeFixture,
) -> None:
    from fugue.engine.locks import TicketLock, TicketLocked

    s = stack_ready
    s.request()
    s.approve()
    deps = s.h.deps
    with (
        TicketLock(deps.state_dir, deps.github.repo, s.tickets[1].number),
        pytest.raises(TicketLocked),
    ):
        s.finish()
    assert s.merges == 0
    assert s.finish().state == RunState.COMPLETED


def test_stack_request_and_approval_survive_store_reopen(tmp_path: Path) -> None:
    from fugue.store import Store

    path = tmp_path / "stack.db"
    heads = '[{"run": 1, "pr": 201, "head": "first"}, {"run": 2, "pr": 202, "head": "second"}]'
    with Store(path) as store:
        parent = store.create_parent_run(170, "owner/repo")
        run = store.create_run(1, "owner/repo")
        store.request_stack_approval(parent.id, heads)
    with Store(path) as store:
        approval = store.record_stack_approval(parent.id, run.id, heads, "developer", "ready")
    with Store(path) as store:
        assert store.stack_approval(parent.id, heads) == approval
        assert store.stack_approval(parent.id, heads.replace("second", "changed")) is None
    with Store(path) as store:
        assert store.stack_approval(parent.id, heads) is None


def test_parent_approval_cli_round_trip(
    stack_ready: StackMergeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from fugue.cli import app as cli
    from fugue.cli import wiring

    s = stack_ready
    engine = Engine(s.h.deps)
    monkeypatch.setattr(wiring, "resolve_repo_root", lambda *_: s.h.deps.repo_root)
    monkeypatch.setattr(cli, "_load", lambda *_: s.h.deps.config)
    monkeypatch.setattr(
        cli,
        "engine_factory",
        lambda *_: SimpleNamespace(
            request_approval=engine.request_approval,
            approve=engine.approve,
            merge_and_close=engine.merge_and_close,
            close=lambda: None,
        ),
    )
    runner = CliRunner()
    run_id = str(s.parent_id)
    result = runner.invoke(cli.app, ["approval", "request", run_id, "--parent"])
    assert result.exit_code == 7, result.output
    result = runner.invoke(
        cli.app,
        [
            "approval",
            "record",
            run_id,
            "--parent",
            "--by",
            "developer",
            "--reason",
            "Whole stack ready",
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli.app, ["merge", run_id, "--parent"])
    assert result.exit_code == 0, result.output
    assert s.merges == 1 and len(s.closed) == 2
