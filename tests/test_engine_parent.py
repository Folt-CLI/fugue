"""Parent runs: children on one feature branch, integration merges and the feature PR."""

import asyncio
import hashlib
import re
import sys
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import permutations
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from test_engine_to_pr import Harness, git_command, result
from test_engine_to_pr import harness as harness

from fugue.agents import AdapterRegistry, AgentRequest, AgentResult, Capability
from fugue.artifacts import ArtifactStore
from fugue.config import FugueConfig
from fugue.engine import Engine, EngineDeps
from fugue.engine.facts import branch_name, feature_branch_name, worktree_path
from fugue.engine.parent import (
    INTEGRATION,
    ParentOutcome,
    _prompt_decisions,
    order_children,
    plan_children,
    run_parent,
)
from fugue.engine.pipeline import _operations
from fugue.engine.review import Decision, Finding
from fugue.github import CheckResult, GitHubError, PullRequest
from fugue.github.attachments import UploadedAttachment
from fugue.github.client import HeadChanged, PostedReview, PrState, ReviewComment, ReviewThread
from fugue.gitops import Git
from fugue.models import (
    AuthenticationMode,
    Component,
    Disposition,
    IssueType,
    Priority,
    Role,
    Size,
    Status,
    Ticket,
)
from fugue.process import run_command
from fugue.store import Store
from fugue.workflow import RunState

PARENT = 32


def child(
    number: int,
    *,
    blocked_by: tuple[int, ...] = (),
    status: Status = Status.TO_DO,
    priority: Priority = Priority.MEDIUM,
) -> Ticket:
    return Ticket(
        number=number,
        title=f"Child {number}",
        issue_type=IssueType.FEATURE,
        size=Size.S,
        status=status,
        priority=priority,
        milestone="0.1.0",
        assignees=["developer"],
        components=[Component.SOURCE_CODE],
        blocked_by=list(blocked_by),
        body_sections={
            "Summary": f"Deliver child {number}",
            "Scope": "Engine",
            "Solution": (
                f"[{number}-solution-1.md](https://github.com/user-attachments/files/{number})"
            ),
        },
    )


# ---------------------------------------------------------------- ordering and planning


@pytest.mark.parametrize(
    ("children", "expected"),
    [
        ([child(5), child(6), child(7, blocked_by=(5,))], [5, 6, 7]),
        ([child(5, blocked_by=(7,)), child(6), child(7)], [6, 7, 5]),
        ([child(5), child(6), child(7)], [5, 6, 7]),
        ([child(5, blocked_by=(6, 7)), child(6, blocked_by=(7,)), child(7)], [7, 6, 5]),
        ([child(5, blocked_by=(7, 7, 99)), child(6), child(7)], [6, 7, 5]),
    ],
)
def test_dependency_order_is_independent_of_input_order(
    children: list[Ticket], expected: list[int]
) -> None:
    for sequence in permutations(children):
        assert [item.number for item in order_children(sequence)] == expected


@pytest.mark.parametrize(
    ("children", "expected"),
    [
        # Priority decides among ready children, then the ticket number.
        (
            [
                child(5, priority=Priority.LOW),
                child(6, priority=Priority.URGENT),
                child(7, priority=Priority.HIGH),
                child(8, priority=Priority.HIGH),
                child(9),
            ],
            [6, 7, 8, 9, 5],
        ),
        # A blocker still starts first, even when its dependent is more urgent.
        (
            [
                child(5, priority=Priority.LOW),
                child(6, blocked_by=(5,), priority=Priority.URGENT),
                child(7, priority=Priority.MEDIUM),
            ],
            [7, 5, 6],
        ),
        # A dependent that becomes ready competes by Priority with the ready ones.
        (
            [
                child(5),
                child(6, blocked_by=(5,), priority=Priority.URGENT),
                child(7, priority=Priority.LOW),
                child(8, priority=Priority.HIGH),
            ],
            [8, 5, 6, 7],
        ),
    ],
)
def test_ready_children_start_by_priority_then_number(
    children: list[Ticket], expected: list[int]
) -> None:
    for sequence in permutations(children):
        assert [item.number for item in order_children(sequence)] == expected


def test_finished_children_and_their_dependencies_are_ignored() -> None:
    done = child(5, blocked_by=(7,), status=Status.DONE)
    closed = child(6, blocked_by=(7,)).model_copy(update={"state": "closed"})
    remaining = child(7, blocked_by=(5, 6, 99))
    assert order_children([done, closed, remaining]) == [remaining]
    assert order_children([done, closed]) == []


@pytest.mark.parametrize(
    ("children", "numbers"),
    [
        ([child(5, blocked_by=(5,))], [5]),
        ([child(5, blocked_by=(7,)), child(6), child(7, blocked_by=(5,))], [5, 7]),
        (
            [child(5, blocked_by=(6,)), child(6, blocked_by=(7,)), child(7, blocked_by=(5,))],
            [5, 6, 7],
        ),
    ],
)
def test_cycles_raise_with_ticket_numbers(children: list[Ticket], numbers: list[int]) -> None:
    with pytest.raises(ValueError, match="cycle") as error:
        plan_children(children)
    for number in numbers:
        assert str(number) in str(error.value)


def test_plan_separates_sibling_blockers_from_outside_ones() -> None:
    done = child(30, status=Status.DONE)
    plan = plan_children(
        [child(33), child(34, blocked_by=(33, 30)), child(45, blocked_by=(33, 99)), done]
    )
    assert plan.order == (33, 34, 45)
    assert plan.blockers == {33: frozenset(), 34: frozenset({33}), 45: frozenset({33})}
    assert plan.outside == {45: (99,)}


# ---------------------------------------------------------------- fakes


@dataclass
class FakePr:
    number: int
    head: str
    base: str
    title: str
    body: str
    state: str = "OPEN"
    merged_head: str | None = None
    merge_sha: str | None = None

    @property
    def url(self) -> str:
        return f"https://example.test/pr/{self.number}"


def scope(number: int) -> str:
    return (
        "You may touch only these files:\n"
        f"- `child-{number}.txt`\n- `shared.txt`\n\nImplement the change."
    )


class ParentGitHub:
    """A GitHub stand-in whose pull requests merge for real in the bare origin."""

    def __init__(self, origin: Path, catalog: dict[int, Ticket]) -> None:
        self.repo = "owner/repo"
        self.origin = origin
        self.catalog = catalog
        self.prs: list[FakePr] = []
        self.failing: set[str] = set()
        self.merges: list[tuple[int, str]] = []
        self.lose_merge: set[str] = set()
        self.lose_create: set[str] = set()
        self.comment_bodies: dict[int, list[str]] = {}
        self.dispositions: dict[int, Disposition] = {}
        self.closed: list[int] = []
        self.posted: list[PostedReview] = []
        self.uploaded: dict[str, bytes] = {}

    async def _sha(self, branch: str) -> str:
        return await git_command(self.origin, "rev-parse", f"refs/heads/{branch}")

    def _record(self, pr: PullRequest) -> FakePr:
        return next(item for item in self.prs if item.number == pr.number)

    async def get_ticket(self, number: int) -> Ticket:
        return self.catalog[number]

    async def set_status(self, number: int, status: Status) -> None:
        self.catalog[number] = self.catalog[number].model_copy(update={"status": status})

    async def set_disposition(self, number: int, disposition: Disposition) -> None:
        self.dispositions[number] = disposition

    async def close_issue(self, number: int) -> None:
        self.closed.append(number)

    async def comment(self, number: int, body: str) -> None:
        self.comment_bodies.setdefault(number, []).append(body)

    async def comments(self, number: int) -> list[str]:
        return list(self.comment_bodies.get(number, []))

    async def children(self, number: int) -> list[Ticket]:
        return []

    async def download_attachment(self, url: str, dest: Path) -> Path:
        if url in self.uploaded:
            dest.write_bytes(self.uploaded[url])
            return dest
        dest.write_text(scope(int(url.rstrip("/").rsplit("/", 1)[1])))
        return dest

    # Analysis publication and selection ----------------------------------------------

    async def upload_attachments(self, paths: Sequence[Path]) -> list[UploadedAttachment]:
        out = []
        for path in paths:
            content = path.read_bytes()
            url = f"https://github.com/user-attachments/files/u{len(self.uploaded)}/{path.name}"
            self.uploaded[url] = content
            out.append(UploadedAttachment(path.name, url, hashlib.sha256(content).hexdigest()))
        return out

    async def get_body(self, number: int) -> str:
        sections = self.catalog[number].body_sections
        return "\n\n".join(f"## {name}\n\n{text}" for name, text in sections.items())

    async def edit_body(self, number: int, body: str) -> None:
        sections: dict[str, str] = {}
        for block in body.split("\n## "):
            name, _, text = block.removeprefix("## ").partition("\n")
            sections[name] = text.strip()
        self.catalog[number] = self.catalog[number].model_copy(update={"body_sections": sections})

    async def find_pr(self, branch: str) -> PullRequest | None:
        found = [pr for pr in self.prs if pr.head == branch and pr.state == "OPEN"]
        return PullRequest(found[0].number, found[0].url) if found else None

    async def create_pr(
        self,
        branch: str,
        base: str,
        title: str,
        body: str,
        assignee: str,
        milestone: str,
        project_title: str,
    ) -> PullRequest:
        assert await self.find_pr(branch) is None
        record = FakePr(101 + len(self.prs), branch, base, title, body)
        self.prs.append(record)
        if branch in self.lose_create:
            self.lose_create.discard(branch)
            raise ConnectionError("create response lost")
        return PullRequest(record.number, record.url)

    async def pr_head_sha(self, pr: PullRequest) -> str:
        record = self._record(pr)
        return record.merged_head or await self._sha(record.head)

    async def pr_state(self, pr: PullRequest) -> PrState:
        record = self._record(pr)
        return PrState(record.state, await self.pr_head_sha(pr), record.merge_sha)

    async def checks(self, pr: PullRequest) -> list[CheckResult]:
        failing = self._record(pr).head in self.failing
        return [CheckResult("CI", "fail" if failing else "pass", "")]

    async def failed_log_tail(self, run_link: str, max_lines: int = 200) -> str:
        return "runner lost"

    async def rerun_failed(self, run_link: str) -> None:
        pass

    async def merge(self, pr: PullRequest, expected_head_sha: str, method: str = "squash") -> None:
        record = self._record(pr)
        assert method == "merge"
        head = await self._sha(record.head)
        if head != expected_head_sha:
            raise HeadChanged(expected_head_sha, head)
        base = await self._sha(record.base)
        tree = await run_command(["git", "merge-tree", "--write-tree", base, head], cwd=self.origin)
        if tree.returncode != 0:
            raise GitHubError(("gh", "pr", "merge"), "merge conflict")
        merge_sha = await git_command(
            self.origin,
            "-c",
            "user.name=Test Developer",
            "-c",
            "user.email=test@example.test",
            "commit-tree",
            tree.stdout.split()[0],
            "-p",
            base,
            "-p",
            head,
            "-m",
            f"Merge pull request #{record.number}",
        )
        await git_command(
            self.origin, "update-ref", f"refs/heads/{record.base}", merge_sha.strip(), base
        )
        record.state = "MERGED"
        record.merged_head = head
        record.merge_sha = merge_sha.strip()
        self.merges.append((record.number, method))
        if record.head in self.lose_merge:
            self.lose_merge.discard(record.head)
            raise ConnectionError("merge response lost")

    async def reviews(self, pr: PullRequest) -> list[PostedReview]:
        return list(self.posted)

    async def post_review(
        self,
        pr: PullRequest,
        head_sha: str,
        body: str,
        comments: list[ReviewComment] | tuple[ReviewComment, ...] = (),
        event: str = "COMMENT",
    ) -> PostedReview:
        posted = PostedReview(len(self.posted) + 1, head_sha, "COMMENTED", body)
        self.posted.append(posted)
        return posted

    async def review_threads(self, pr: PullRequest) -> list[ReviewThread]:
        return []

    async def unresolved_thread_count(self, pr: PullRequest) -> int:
        return 0

    async def reply_to_thread(self, pr: PullRequest, comment_id: int, body: str) -> None:
        pass

    async def resolve_thread(self, thread_id: str) -> None:
        pass

    async def _paginated(self, endpoint: str, *args: str) -> list[dict[str, Any]]:
        base = unquote(endpoint.split("base=", 1)[1].split("&", 1)[0])
        return [{"number": pr.number} for pr in self.prs if pr.base == base and pr.state == "OPEN"]

    async def delete_remote_branch(self, branch: str, repo_root: Path) -> None:
        if (await git_command(repo_root, "ls-remote", "--heads", "origin", branch)).strip():
            await git_command(repo_root, "push", "--delete", "origin", f"refs/heads/{branch}")


Hook = Callable[[Path], Awaitable[None]]


@dataclass
class Agents:
    """Implement each child in its own file and review cleanly, in any order."""

    name: str = ""
    authentication_mode: AuthenticationMode = AuthenticationMode.SUBSCRIPTION_LOGIN
    capabilities: frozenset[Capability] = frozenset(Capability)
    implemented: list[int] = field(default_factory=list)
    reviewed: list[int] = field(default_factory=list)
    on_implement: dict[int, Hook] = field(default_factory=dict)
    on_review: dict[int, Hook] = field(default_factory=dict)
    shared: dict[int, str] = field(default_factory=dict)
    resolution: str | None = None
    resolved: list[int] = field(default_factory=list)

    async def run(self, request: AgentRequest, on_event: object = None) -> AgentResult:
        number = int(request.cwd.name.split("-", 1)[0])
        if request.role == Role.IMPLEMENTER and "stopped on conflicts" in request.prompt:
            self.resolved.append(number)
            if self.resolution is not None:
                (request.cwd / "shared.txt").write_text(self.resolution)
            return result()
        if request.role == Role.IMPLEMENTER:
            self.implemented.append(number)
            hook = self.on_implement.pop(number, None)
            if hook is not None:
                await hook(request.cwd)
            (request.cwd / f"child-{number}.txt").write_text(f"child {number}\n")
            if number in self.shared:
                (request.cwd / "shared.txt").write_text(self.shared[number])
            return result()
        self.reviewed.append(number)
        hook = self.on_review.pop(number, None)
        if hook is not None:
            await hook(request.cwd)
        return result(final_text="No findings.")


@dataclass
class Parent:
    deps: EngineDeps
    github: ParentGitHub
    agents: Agents
    origin: Path
    develop: str

    def run(self, **options: Any) -> ParentOutcome:
        children = [t for n, t in self.github.catalog.items() if n != PARENT]
        return asyncio.run(run_parent(self.deps, PARENT, children, **options))

    def add(self, *tickets: Ticket) -> None:
        for ticket in tickets:
            self.github.catalog[ticket.number] = ticket

    @property
    def feature(self) -> str:
        return feature_branch_name(self.github.catalog[PARENT])

    def path(self, number: int) -> Path:
        return worktree_path(self.deps.worktrees_root, self.github.catalog[number])

    def child_pr(self, number: int) -> FakePr:
        branch = branch_name(self.github.catalog[number])
        return next(pr for pr in self.github.prs if pr.head == branch)

    def feature_prs(self) -> list[FakePr]:
        return [pr for pr in self.github.prs if pr.head == self.feature]

    def origin_has(self, ref: str, path: str) -> bool:
        output = asyncio.run(
            run_command(["git", "cat-file", "-e", f"{ref}:{path}"], cwd=self.origin)
        )
        return output.returncode == 0

    def run_id(self, outcome: ParentOutcome, number: int) -> int:
        state = next(item for item in outcome.children if item.number == number)
        assert state.run_id is not None
        return state.run_id


def states(outcome: ParentOutcome) -> dict[int, str]:
    return {item.number: item.state for item in outcome.children}


@pytest.fixture
def parent(tmp_path: Path) -> Iterator[Parent]:
    async def setup() -> tuple[Path, Path, str]:
        origin = tmp_path / "origin.git"
        clone = tmp_path / "clone"
        await git_command(tmp_path, "init", "--bare", "--initial-branch=develop", str(origin))
        await git_command(tmp_path, "clone", str(origin), str(clone))
        await git_command(clone, "config", "user.name", "Test Developer")
        await git_command(clone, "config", "user.email", "test@example.test")
        (clone / "README").write_text("Initial")
        (clone / "shared.txt").write_text("base\n")
        await git_command(clone, "add", "README", "shared.txt")
        await git_command(clone, "commit", "-m", "Initial")
        await git_command(clone, "push", "origin", "develop")
        return origin, clone, await Git(clone).head_sha()

    origin, clone, head = asyncio.run(setup())
    question = Ticket(
        number=PARENT,
        title="Deliver the feature",
        issue_type=IssueType.QUESTION,
        size=Size.L,
        status=Status.IN_IMPLEMENTATION,
        priority=Priority.HIGH,
        milestone="0.1.0",
        assignees=["developer"],
        body_sections={"Summary": "Deliver the whole feature"},
    )
    github = ParentGitHub(origin, {PARENT: question})
    config = FugueConfig()
    # Both roles use the default provider; one stand-in serves them by role.
    provider = config.roles[Role.IMPLEMENTER].provider
    assert config.roles[Role.REVIEWER].provider == provider
    agents = Agents(name=provider)
    registry = AdapterRegistry()
    registry.register(provider, lambda config: agents)
    config.policy.gate_commands = [[sys.executable, "-c", "print('gate passed')"]]
    config.limits.ci_poll_interval_seconds = 1
    with Store(":memory:") as store:
        deps = EngineDeps(
            config,
            store,
            ArtifactStore(tmp_path / "artifacts"),
            github,
            registry,
            clone,
            tmp_path / "worktrees",
            tmp_path / "state",
        )
        yield Parent(deps, github, agents, origin, head)


def gates(p: Parent, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the ticket of every gate run."""
    seen: list[int] = []
    original = Engine._gate

    async def gate(self: Engine, run_id: int, path: Path) -> Any:
        seen.append(p.deps.store.get_run(run_id).ticket_number)
        return await original(self, run_id, path)

    monkeypatch.setattr(Engine, "_gate", gate)
    return seen


def barrier(*numbers: int) -> dict[int, Hook]:
    """Hooks that return only once every listed child is implementing at the same time."""
    started = {number: asyncio.Event() for number in numbers}

    def hook(number: int) -> Hook:
        async def wait(path: Path) -> None:
            started[number].set()
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in started.values())), timeout=20
            )

        return wait

    return {number: hook(number) for number in numbers}


def wait_until_merged(p: Parent, number: int) -> Hook:
    async def wait(path: Path) -> None:
        for _ in range(400):
            prs = [pr for pr in p.github.prs if pr.head == branch_name(p.github.catalog[number])]
            if prs and prs[0].state == "MERGED":
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"#{number} never merged")

    return wait


# ---------------------------------------------------------------- feature branch


def test_feature_branch_starts_at_develop_and_is_reused(parent: Parent) -> None:
    p = parent
    p.add(child(33))
    first = p.run(authorise_merge=True, by="developer")
    assert first.state == RunState.WAITING_FOR_MERGE_APPROVAL, first.problems
    assert first.feature_branch == "feature/32-deliver-the-feature"
    assert p.child_pr(33).base == p.feature
    develop = asyncio.run(git_command(p.origin, "rev-parse", "refs/heads/develop"))
    assert develop == p.develop
    base = asyncio.run(git_command(p.origin, "merge-base", p.feature, "develop"))
    assert base == p.develop
    assert len(p.feature_prs()) == 1
    feature_head = asyncio.run(git_command(p.origin, "rev-parse", p.feature))
    again = p.run(authorise_merge=True, by="developer")
    assert again.state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert again.parent_run_id == first.parent_run_id
    assert again.feature_run_id == first.feature_run_id
    assert asyncio.run(git_command(p.origin, "rev-parse", p.feature)) == feature_head
    assert len(p.github.prs) == 2
    assert p.agents.implemented == [33]


def test_an_existing_remote_feature_branch_is_reused(parent: Parent) -> None:
    p = parent
    p.add(child(33))

    async def publish() -> str:
        await git_command(p.deps.repo_root, "branch", p.feature, "develop")
        (p.deps.repo_root / "README").write_text("Earlier feature work")
        await git_command(p.deps.repo_root, "switch", p.feature)
        await git_command(p.deps.repo_root, "commit", "-am", "Earlier work")
        await git_command(p.deps.repo_root, "push", "origin", p.feature)
        head = await Git(p.deps.repo_root).head_sha()
        await git_command(p.deps.repo_root, "switch", "develop")
        await git_command(p.deps.repo_root, "branch", "-D", p.feature)
        return head

    earlier = asyncio.run(publish())
    outcome = p.run(authorise_merge=True)
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL, outcome.problems
    ancestry = asyncio.run(
        run_command(["git", "merge-base", "--is-ancestor", earlier, p.feature], cwd=p.origin)
    )
    assert ancestry.returncode == 0


# ---------------------------------------------------------------- readiness and order


def test_sequential_children_start_from_a_feature_head_holding_their_blockers(
    parent: Parent,
) -> None:
    p = parent
    p.add(child(45, blocked_by=(33,)), child(34, blocked_by=(33,)), child(33))
    seen: dict[int, bool] = {}

    def check(number: int) -> Hook:
        async def look(path: Path) -> None:
            seen[number] = (path / "child-33.txt").exists()

        return look

    p.agents.on_implement = {34: check(34), 45: check(45)}
    outcome = p.run(authorise_merge=True, by="developer")
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL, outcome.problems
    assert p.agents.implemented == [33, 34, 45]
    assert seen == {34: True, 45: True}
    assert states(outcome) == {33: "merged", 34: "merged", 45: "merged"}
    assert {pr.base for pr in p.github.prs if pr.head != p.feature} == {p.feature}
    assert all(method == "merge" for _, method in p.github.merges)
    for number in (33, 34, 45):
        assert p.origin_has(p.feature, f"child-{number}.txt")
        assert p.github.catalog[number].status == Status.DONE
    assert not p.origin_has("develop", "child-33.txt")
    [feature_pr] = p.feature_prs()
    assert feature_pr.base == "develop"
    assert "Closes #33" in feature_pr.body and "Part of #32" in feature_pr.body
    assert feature_pr.title == "Question[#32]: Deliver the feature"


def test_analysis_root_holds_every_sibling_merged_before_the_child_starts(parent: Parent) -> None:
    p = parent
    p.add(child(34, blocked_by=(33,)), child(33))
    seen: dict[int, bool] = {}

    async def look(path: Path) -> None:
        # The feature worktree is the code root of analysis and scoping.
        seen[34] = (p.path(PARENT) / "child-33.txt").exists()

    p.agents.on_implement = {34: look}
    outcome = p.run(authorise_merge=True, by="developer")
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL, outcome.problems
    assert seen == {34: True}


def test_parallel_children_run_together_from_the_same_feature_head(
    parent: Parent, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = parent
    p.deps.config.limits.parallel_children = 2
    p.add(child(33), child(34, blocked_by=(33,)), child(45, blocked_by=(33,)))
    seen: dict[int, bool] = {}
    hooks = barrier(34, 45)

    def check(number: int) -> Hook:
        async def look(path: Path) -> None:
            seen[number] = (path / "child-33.txt").exists()
            await hooks[number](path)

        return look

    p.agents.on_implement = {34: check(34), 45: check(45)}
    p.agents.on_review = {45: wait_until_merged(p, 34)}
    ran = gates(p, monkeypatch)
    outcome = p.run(authorise_merge=True)
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL, outcome.problems
    assert seen == {34: True, 45: True}
    assert states(outcome) == {33: "merged", 34: "merged", 45: "merged"}
    # 34 merged first, so 45 merged the moved feature branch, ran its gate and review again.
    later = p.run_id(outcome, 45)
    updates = _operations(p.deps.store, later, "feature_update")
    assert len(updates) == 1 and updates[0][2] == "done"
    assert _operations(p.deps.store, p.run_id(outcome, 34), "feature_update") == []
    assert p.agents.reviewed.count(45) == 2
    assert ran.count(45) > ran.count(34)
    subjects = asyncio.run(git_command(p.path(45), "log", "--format=%s", "-3"))
    assert "Feature[#45]: Merge the feature branch" in subjects.splitlines()
    merged = p.child_pr(45).merged_head
    assert merged is not None
    assert (
        asyncio.run(
            run_command(
                ["git", "merge-base", "--is-ancestor", p.child_pr(34).merge_sha or "", merged],
                cwd=p.origin,
            )
        ).returncode
        == 0
    )


def test_gate_failure_after_an_update_is_repaired_and_finishes_in_the_same_run(
    parent: Parent, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fugue.gate import GateResult

    p = parent
    p.deps.config.limits.parallel_children = 2
    p.add(child(33), child(34, blocked_by=(33,)), child(45, blocked_by=(33,)))
    p.agents.on_implement = barrier(34, 45)
    p.agents.on_review = {45: wait_until_merged(p, 34)}
    original = Engine._gate
    failing = [True]

    async def gate(self: Engine, run_id: int, path: Path) -> GateResult:
        pending = [
            row for row in _operations(p.deps.store, run_id, "feature_update") if row[2] != "done"
        ]
        if pending and failing[0]:
            failing[0] = False
            return GateResult(False, (), None)
        return await original(self, run_id, path)

    monkeypatch.setattr(Engine, "_gate", gate)
    finished = p.run(authorise_merge=True)
    assert states(finished) == {33: "merged", 34: "merged", 45: "merged"}, finished.problems
    assert p.origin_has(p.feature, "child-45.txt")


def test_sequential_limit_never_runs_two_children_at_once(parent: Parent) -> None:
    p = parent
    p.add(child(33), child(34), child(45))
    active: list[int] = []
    peak: list[int] = []

    def track(number: int) -> Hook:
        async def look(path: Path) -> None:
            active.append(number)
            peak.append(len(active))
            await asyncio.sleep(0.05)
            active.remove(number)

        return look

    p.agents.on_implement = {n: track(n) for n in (33, 34, 45)}
    outcome = p.run(authorise_merge=True)
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL, outcome.problems
    assert p.agents.implemented == [33, 34, 45]
    assert max(peak) == 1


def test_diamond_waits_for_both_blockers(parent: Parent) -> None:
    p = parent
    p.deps.config.limits.parallel_children = 3
    p.add(
        child(33),
        child(34, blocked_by=(33,)),
        child(35, blocked_by=(33,)),
        child(36, blocked_by=(34, 35)),
    )
    seen: dict[str, bool] = {}

    async def look(path: Path) -> None:
        seen["34"] = (path / "child-34.txt").exists()
        seen["35"] = (path / "child-35.txt").exists()

    p.agents.on_implement = {36: look}
    outcome = p.run(authorise_merge=True)
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL, outcome.problems
    assert p.agents.implemented[-1] == 36
    assert seen == {"34": True, "35": True}
    assert set(states(outcome).values()) == {"merged"}
    assert len(p.agents.implemented) == 4


def test_chain_runs_in_order(parent: Parent) -> None:
    p = parent
    p.deps.config.limits.parallel_children = 3
    p.add(child(35, blocked_by=(34,)), child(34, blocked_by=(33,)), child(33))
    outcome = p.run(authorise_merge=True)
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL, outcome.problems
    assert p.agents.implemented == [33, 34, 35]
    assert set(states(outcome).values()) == {"merged"}


# ---------------------------------------------------------------- failures and conflicts


def test_conflict_stops_one_child_while_independent_children_finish(parent: Parent) -> None:
    p = parent
    p.deps.config.limits.parallel_children = 3
    p.add(
        child(33),
        child(34, blocked_by=(33,)),
        child(45, blocked_by=(33,)),
        child(46),
        child(47, blocked_by=(45,)),
    )
    p.agents.shared = {34: "from 34\n", 45: "from 45\n"}
    p.agents.on_implement = barrier(34, 45)
    p.agents.on_review = {45: wait_until_merged(p, 34)}
    outcome = p.run(authorise_merge=True)
    assert outcome.state == RunState.BLOCKED
    assert states(outcome) == {
        33: "merged",
        34: "merged",
        45: RunState.BLOCKED,
        46: "merged",
        47: "not_started",
    }
    [problem] = [problem for problem in outcome.problems if problem.startswith("#45")]
    assert "conflicts with feature/32-deliver-the-feature" in problem
    assert next(item for item in outcome.children if item.number == 47).waits_for == (45,)
    assert p.feature_prs() == []
    assert 47 not in p.agents.implemented
    assert asyncio.run(Git(p.path(45)).status_porcelain()) == []
    # Running again stops at the same conflict, without repeating merged children.
    again = p.run(authorise_merge=True)
    assert states(again)[45] == RunState.BLOCKED
    assert len(p.github.merges) == 3


def test_agent_resolves_a_conflict_between_children(parent: Parent) -> None:
    p = parent
    p.deps.config.limits.parallel_children = 3
    p.add(child(33), child(34, blocked_by=(33,)), child(45, blocked_by=(33,)))
    p.agents.shared = {34: "from 34\n", 45: "from 45\n"}
    p.agents.resolution = "from 34 and 45\n"
    p.agents.on_implement = barrier(34, 45)
    p.agents.on_review = {45: wait_until_merged(p, 34)}
    outcome = p.run(authorise_merge=True)
    assert p.agents.resolved == [45]
    assert outcome.problems == ()
    assert states(outcome) == {33: "merged", 34: "merged", 45: "merged"}
    assert asyncio.run(Git(p.path(45)).status_porcelain()) == []


def test_a_failing_child_stops_only_its_dependents(
    parent: Parent, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = parent
    p.add(child(33), child(34, blocked_by=(33,)), child(45))
    original = Engine.run_to_pr

    async def fail(self: Engine, number: int) -> Any:
        if number == 33:
            run = self._find_or_create_run(number)
            p.deps.store.set_run_state(run.id, RunState.FAILED)
            from fugue.engine import RunOutcome

            return RunOutcome(run.id, RunState.FAILED, problems=("Implementer failed",))
        return await original(self, number)

    monkeypatch.setattr("fugue.engine.parent._WorkEngine.run_to_pr", fail)
    outcome = p.run(authorise_merge=True)
    assert outcome.state == RunState.FAILED
    assert states(outcome) == {33: RunState.FAILED, 34: "not_started", 45: "merged"}
    assert "#33: Implementer failed" in outcome.problems
    assert "ticket #34: not started; waits for #33" in (outcome.summary or "")


def test_open_blocker_outside_the_parent_blocks_only_that_child(parent: Parent) -> None:
    p = parent
    outside = child(99)
    p.add(child(33), child(34, blocked_by=(99,)), child(45, blocked_by=(98,)))
    p.github.catalog[99] = outside
    p.github.catalog[98] = child(98, status=Status.DONE)
    children = [p.github.catalog[n] for n in (33, 34, 45)]
    outcome = asyncio.run(run_parent(p.deps, PARENT, children, authorise_merge=True))
    assert states(outcome) == {33: "merged", 34: RunState.BLOCKED, 45: "merged"}
    assert any("blocked by #99, not a child of #32" in problem for problem in outcome.problems)
    assert 34 not in p.agents.implemented


def test_unanswered_question_child_waits_and_dependents_wait(parent: Parent) -> None:
    p = parent
    # An L Question is answered in its interview, never by the run.
    question = child(33).model_copy(update={"issue_type": IssueType.QUESTION, "size": Size.L})
    p.add(question, child(34, blocked_by=(33,)), child(45))
    outcome = p.run(authorise_merge=True)
    assert outcome.state == RunState.WAITING_FOR_DECISION
    assert outcome.next_step == "fugue question 33"
    assert states(outcome) == {33: RunState.WAITING_FOR_DECISION, 34: "not_started", 45: "merged"}


# ---------------------------------------------------------------- analysis batches


def unscoped(
    number: int,
    *,
    blocked_by: tuple[int, ...] = (),
    priority: Priority = Priority.MEDIUM,
    size: Size = Size.S,
) -> Ticket:
    """A child that carries no scope, so its run analyses it first."""
    ticket = child(number, blocked_by=blocked_by, priority=priority)
    sections = {**ticket.body_sections, "Solution": "_Not applicable._"}
    return ticket.model_copy(update={"size": size, "body_sections": sections})


@dataclass
class Analyst:
    """The analyst: one handoff per ticket, alone or in an analysis batch."""

    github: ParentGitHub
    name: str = "claude"
    authentication_mode: AuthenticationMode = AuthenticationMode.SUBSCRIPTION_LOGIN
    capabilities: frozenset[Capability] = frozenset(Capability)
    calls: list[tuple[str, list[int]]] = field(default_factory=list)
    merged_at_call: list[list[str]] = field(default_factory=list)
    fire: set[int] = field(default_factory=set)
    drop: set[int] = field(default_factory=set)
    invalid: set[int] = field(default_factory=set)

    def handoff(self, number: int) -> dict[str, Any]:
        from test_analysis import handoff

        data = handoff().model_dump(mode="json")
        data["scope"]["allowed_files"] = [f"child-{number}.txt", "shared.txt"]
        data["triggers"] = [
            {"name": name, "fired": number in self.fire and name == "behaviour", "evidence": "-"}
            for name in ("ambiguity", "behaviour", "architecture", "irreversible")
        ]
        return data

    async def run(self, request: AgentRequest, on_event: object = None) -> AgentResult:
        from fugue.analysis.schema import batch_schema

        assert request.role == Role.ANALYST
        self.merged_at_call.append([pr.head for pr in self.github.prs if pr.state == "MERGED"])
        output: dict[str, Any]
        if request.output_schema == batch_schema():
            numbers = [int(n) for n in re.findall(r"Ticket #(\d+):", request.prompt)]
            self.calls.append(("batch", numbers))
            output = {
                "analyses": [
                    {
                        "ticket": number,
                        "handoff": {"decision": ""}
                        if number in self.invalid
                        else self.handoff(number),
                    }
                    for number in numbers
                    if number not in self.drop
                ]
            }
        else:
            match = re.search(r'"number":(\d+)', request.prompt)
            assert match is not None
            number = int(match[1])
            self.calls.append(("single", [number]))
            output = self.handoff(number)
        return AgentResult(
            ok=True,
            final_text="",
            structured_output=output,
            session_id=None,
            usage=result().usage,
            provider="claude",
            authentication_mode=AuthenticationMode.SUBSCRIPTION_LOGIN,
            error=None,
            timed_out=False,
            cancelled=False,
            log_path=None,
            unsupported=None,
        )


def analyst(p: Parent) -> Analyst:
    agent = Analyst(p.github)
    p.deps.registry.register("claude", lambda config: agent)
    return agent


def analyst_records(p: Parent) -> int:
    return sum(row.records for row in p.deps.store.usage_by("role") if row.key == "analyst")


def test_ready_s_and_xs_children_are_analysed_in_one_batch_by_priority(parent: Parent) -> None:
    p = parent
    agent = analyst(p)
    p.add(
        unscoped(33, priority=Priority.LOW),
        unscoped(34, size=Size.XS),
        unscoped(35, priority=Priority.URGENT),
        unscoped(36),
        unscoped(37, priority=Priority.HIGH, size=Size.XS),
        unscoped(38),
    )
    outcome = p.run()
    # The five most urgent form the batch; the sixth is a batch of one, analysed alone.
    assert agent.calls == [("batch", [35, 37, 34, 36, 38]), ("single", [33])]
    assert analyst_records(p) == 2
    assert all(state == RunState.WAITING_FOR_MERGE_APPROVAL for state in states(outcome).values())
    for number in range(33, 39):
        ticket = p.github.catalog[number]
        assert "[Selected Solution]" in ticket.body_sections["Solution"]
        assert "by policy: no trigger fired" in p.github.comment_bodies[number][0]
        assert f"{number}-code-findings.md" in ticket.body_sections["Details"]
    assert sorted(p.agents.implemented) == list(range(33, 39))


def test_the_batch_size_splits_the_ready_children_into_batches(parent: Parent) -> None:
    p = parent
    p.deps.config.limits.analysis_batch_size = 2
    agent = analyst(p)
    p.add(unscoped(33), unscoped(34), unscoped(35), unscoped(36))
    p.run()
    assert agent.calls == [("batch", [33, 34]), ("batch", [35, 36])]


def test_a_blocked_child_is_analysed_only_after_its_blocker_is_merged(parent: Parent) -> None:
    p = parent
    agent = analyst(p)
    p.add(
        unscoped(33), unscoped(34, blocked_by=(33,)), unscoped(35), unscoped(36, blocked_by=(99,))
    )
    p.github.catalog[99] = child(99)
    children = [p.github.catalog[n] for n in (33, 34, 35, 36)]
    outcome = asyncio.run(run_parent(p.deps, PARENT, children, authorise_merge=True))
    assert agent.calls == [("batch", [33, 35]), ("single", [34])]
    assert branch_name(p.github.catalog[33]) in agent.merged_at_call[1]
    assert states(outcome)[34] == "merged" and states(outcome)[36] == RunState.BLOCKED
    assert p.github.catalog[36].status == Status.TO_DO


def test_an_invalid_or_missing_handoff_is_analysed_again_alone(parent: Parent) -> None:
    p = parent
    agent = analyst(p)
    agent.invalid, agent.drop = {34}, {35}
    p.add(unscoped(33), unscoped(34), unscoped(35))
    outcome = p.run()
    assert agent.calls == [("batch", [33, 34, 35]), ("single", [34]), ("single", [35])]
    assert all(state == RunState.WAITING_FOR_MERGE_APPROVAL for state in states(outcome).values())


def test_a_fired_trigger_parks_only_that_child(parent: Parent) -> None:
    p = parent
    agent = analyst(p)
    agent.fire = {34}
    p.add(unscoped(33), unscoped(34), unscoped(35))
    outcome = p.run()
    assert states(outcome) == {
        33: RunState.WAITING_FOR_MERGE_APPROVAL,
        34: RunState.WAITING_FOR_SELECTION,
        35: RunState.WAITING_FOR_MERGE_APPROVAL,
    }
    assert p.github.catalog[34].status == Status.IN_ANALYSIS
    assert "[Selected Solution]" not in p.github.catalog[34].body_sections["Solution"]
    assert 34 not in p.agents.implemented
    again = p.run()
    assert len(agent.calls) == 1  # The parked child keeps its saved handoff.
    assert states(again)[34] == RunState.WAITING_FOR_SELECTION


def test_m_children_are_analysed_one_agent_each(parent: Parent) -> None:
    p = parent
    agent = analyst(p)
    p.add(unscoped(33, size=Size.M), unscoped(34), unscoped(35), unscoped(36, size=Size.M))
    p.run()
    assert agent.calls == [("batch", [34, 35]), ("single", [33]), ("single", [36])]


def test_cycle_is_rejected_before_anything_is_created(parent: Parent) -> None:
    p = parent
    p.add(child(33, blocked_by=(34,)), child(34, blocked_by=(33,)))
    with pytest.raises(ValueError, match="cycle"):
        p.run(authorise_merge=True)
    assert p.deps.store.find_open_parent_run(p.github.repo, PARENT) is None
    remote = asyncio.run(git_command(p.origin, "branch", "--list", "feature/*"))
    assert remote == ""
    assert p.agents.implemented == []


# ---------------------------------------------------------------- approval and delivery


def test_without_authorise_each_child_waits_for_its_approval_and_progresses_again(
    parent: Parent,
) -> None:
    p = parent
    p.add(child(33), child(34, blocked_by=(33,)), child(45, blocked_by=(33,)))
    first = p.run()
    assert first.state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert states(first) == {
        33: RunState.WAITING_FOR_MERGE_APPROVAL,
        34: "not_started",
        45: "not_started",
    }
    run_33 = p.run_id(first, 33)
    assert first.next_step == (
        f"fugue approval record {run_33} --reason 'reason text', then fugue run 32"
    )
    assert p.github.merges == []
    from fugue.cli.app import _StoreClosingEngine

    resumed = asyncio.run(_StoreClosingEngine(Engine(p.deps)).resume(run_33))
    assert resumed.state == RunState.BLOCKED
    assert resumed.problems == (
        f"Run {run_33} belongs to the feature branch of #32; continue with fugue run 32",
    )
    repeated = p.run()
    assert states(repeated)[33] == RunState.WAITING_FOR_MERGE_APPROVAL
    assert p.agents.implemented == [33] and p.agents.reviewed == [33]
    Engine(p.deps).approve(run_33, "developer", "Child is ready")
    second = p.run()
    assert states(second) == {
        33: "merged",
        34: RunState.WAITING_FOR_MERGE_APPROVAL,
        45: RunState.WAITING_FOR_MERGE_APPROVAL,
    }
    assert p.feature_prs() == []
    blocked = asyncio.run(Engine(p.deps).merge_and_close(run_33))
    assert blocked.state == RunState.BLOCKED
    assert "fugue run 32" in blocked.problems[0]


def test_authorise_records_exact_head_approvals_for_children_only(parent: Parent) -> None:
    p = parent
    p.add(child(33), child(34, blocked_by=(33,)))
    outcome = p.run(authorise_merge=True, by="authorising-person")
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL
    approvals = p.deps.store.merge_approvals(p.github.repo)
    assert {approval.run_id for approval in approvals} == {
        p.run_id(outcome, 33),
        p.run_id(outcome, 34),
    }
    for approval in approvals:
        assert approval.decided_by == "authorising-person"
        assert approval.reason == "authorised for this run"
        assert (
            approval.head_sha
            == p.child_pr(p.deps.store.get_run(approval.run_id).ticket_number).merged_head
        )
    assert outcome.feature_run_id is not None
    assert outcome.next_step == (
        f"fugue approval record {outcome.feature_run_id} --reason 'reason text', then fugue run 32"
    )
    again = p.run(authorise_merge=True, by="authorising-person")
    assert again.state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert all(pr.state == "OPEN" for pr in p.feature_prs())
    assert len(p.deps.store.merge_approvals(p.github.repo)) == 2


def test_child_is_closed_as_soon_as_it_merges_into_the_feature_branch(parent: Parent) -> None:
    p = parent
    p.add(child(33), child(34, blocked_by=(33,)))
    waiting = p.run(authorise_merge=True)
    assert waiting.state == RunState.WAITING_FOR_MERGE_APPROVAL
    # The feature pull request is still open, yet both children are already closed out.
    [feature_pr] = p.feature_prs()
    assert feature_pr.state == "OPEN"
    for number in (33, 34):
        assert p.github.catalog[number].status == Status.DONE
        assert p.github.dispositions[number] == Disposition.MERGED
        assert number in p.github.closed
        [comment] = p.github.comment_bodies[number]
        assert f"Merged into `{p.feature}`" in comment
    assert p.path(33).exists()
    again = p.run(authorise_merge=True)
    assert again.state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert p.github.closed.count(33) == 1
    assert len(p.github.comment_bodies[33]) == 1


def test_feature_pull_request_merges_only_after_developer_approval(parent: Parent) -> None:
    p = parent
    p.add(child(33), child(34, blocked_by=(33,)))
    waiting = p.run(authorise_merge=True)
    assert waiting.state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert waiting.feature_run_id is not None
    [feature_pr] = p.feature_prs()
    assert feature_pr.state == "OPEN"
    assert asyncio.run(git_command(p.origin, "rev-parse", "develop")) == p.develop
    Engine(p.deps).approve(waiting.feature_run_id, "developer", "Feature is ready")
    done = p.run()
    assert done.state == RunState.COMPLETED, done.problems
    assert feature_pr.state == "MERGED"
    assert p.github.merges[-1] == (feature_pr.number, "merge")
    for number in (33, 34):
        assert p.origin_has("develop", f"child-{number}.txt")
        assert p.github.catalog[number].status == Status.DONE
        assert p.github.dispositions[number] == Disposition.MERGED
        assert len(p.github.comment_bodies[number]) == 2
        assert f"delivered to develop by #{feature_pr.number}" in p.github.comment_bodies[number][1]
        assert not p.path(number).exists()
    assert p.github.catalog[PARENT].status == Status.IN_IMPLEMENTATION
    assert not (p.deps.worktrees_root / "32-deliver-the-feature").exists()
    assert asyncio.run(git_command(p.origin, "branch", "--list", "feature/*")) == ""
    merges = len(p.github.merges)
    finished = p.run()
    assert finished.state == RunState.COMPLETED
    assert len(p.github.merges) == merges


def test_red_ci_on_the_feature_pull_request_stops_before_approval(parent: Parent) -> None:
    p = parent
    p.add(child(33))
    p.github.failing.add(p.feature)
    outcome = p.run(authorise_merge=True)
    assert outcome.state == RunState.BLOCKED
    assert any("CI failed on the feature pull request" in item for item in outcome.problems)
    approvals = p.deps.store.merge_approvals(p.github.repo)
    assert approvals and all(item.run_id != outcome.feature_run_id for item in approvals)


# ---------------------------------------------------------------- resume


def test_lost_child_merge_response_is_reconciled_without_a_second_merge(parent: Parent) -> None:
    p = parent
    p.add(child(33), child(34, blocked_by=(33,)))
    p.github.lose_merge.add(branch_name(p.github.catalog[33]))
    first = p.run(authorise_merge=True)
    assert states(first) == {33: RunState.BLOCKED, 34: "not_started"}
    assert len(p.github.merges) == 1
    second = p.run(authorise_merge=True)
    assert states(second) == {33: "merged", 34: "merged"}, second.problems
    assert [number for number, _ in p.github.merges].count(p.child_pr(33).number) == 1
    run_33 = p.run_id(second, 33)
    assert [row[2] for row in _operations(p.deps.store, run_33, INTEGRATION)] == ["done"]


def test_lost_feature_pull_request_response_creates_one_pull_request(parent: Parent) -> None:
    p = parent
    p.add(child(33))
    p.github.lose_create.add(p.feature)
    with pytest.raises(ConnectionError, match="create response lost"):
        p.run(authorise_merge=True)
    again = p.run(authorise_merge=True)
    assert again.state == RunState.WAITING_FOR_MERGE_APPROVAL, again.problems
    assert len(p.feature_prs()) == 1


def test_lost_feature_merge_response_closes_children_once(parent: Parent) -> None:
    p = parent
    p.add(child(33))
    waiting = p.run(authorise_merge=True)
    assert waiting.feature_run_id is not None
    Engine(p.deps).approve(waiting.feature_run_id, "developer", "Feature is ready")
    p.github.lose_merge.add(p.feature)
    with pytest.raises(ConnectionError, match="merge response lost"):
        p.run()
    assert p.github.catalog[33].status == Status.DONE
    done = p.run()
    assert done.state == RunState.COMPLETED, done.problems
    assert [number for number, _ in p.github.merges].count(p.feature_prs()[0].number) == 1
    assert len(p.github.comment_bodies[33]) == 2


def test_interrupted_child_resumes_the_same_run_and_pull_request(parent: Parent) -> None:
    p = parent
    p.add(child(33), child(34, blocked_by=(33,)))

    async def interrupt(path: Path) -> None:
        raise asyncio.CancelledError

    p.agents.on_implement = {34: interrupt}
    with pytest.raises(asyncio.CancelledError):
        p.run(authorise_merge=True)
    stopped = p.deps.store.find_open_parent_run(p.github.repo, PARENT)
    assert stopped is not None and stopped.state == RunState.INTERRUPTED
    before = {item.ticket_number: item.run_id for item in p.deps.store.list_work(stopped.id)}
    outcome = p.run(authorise_merge=True)
    assert outcome.parent_run_id == stopped.id
    assert states(outcome) == {33: "merged", 34: "merged"}
    assert {item.number: item.run_id for item in outcome.children} == before
    assert len([pr for pr in p.github.prs if pr.head != p.feature]) == 2
    assert p.agents.implemented == [33, 34, 34]


def test_parent_without_open_children_completes_without_a_feature_branch(parent: Parent) -> None:
    p = parent
    outcome = p.run()
    assert outcome.state == RunState.COMPLETED
    assert asyncio.run(git_command(p.origin, "branch", "--list", "feature/*")) == ""


# ---------------------------------------------------------------- single tickets


def test_single_ticket_with_an_open_blocker_blocks_before_any_worktree(harness: Harness) -> None:
    h = harness
    h.github.ticket = h.github.ticket.model_copy(update={"blocked_by": [7]})
    outcome = h.run()
    assert outcome.state == RunState.BLOCKED
    assert outcome.problems == ("Open blockers must merge first: #7; or run their parent",)
    assert not h.deps.worktrees_root.exists()


def test_single_ticket_still_targets_the_base_branch(harness: Harness) -> None:
    h = harness
    assert h.run().state == RunState.WAITING_FOR_CI
    assert h.github.created[0][1] == "develop"
    assert "feature branch" not in h.github.created[0][3]


# ---------------------------------------------------------------- decisions


@pytest.mark.parametrize(
    ("choice", "answer", "blocking", "expected"),
    [
        (1, "Reason", True, {1: Decision(False, reason="Reason")}),
        (1, "  ", True, None),
        (2, "42", False, {1: Decision(False, deferred_to=42)}),
        (2, "0", False, None),
        (2, "invalid", False, None),
    ],
)
def test_review_choice_validates_reason_and_deferred_ticket(
    monkeypatch: pytest.MonkeyPatch,
    choice: int,
    answer: str,
    blocking: bool,
    expected: dict[int, Decision] | None,
) -> None:
    from fugue.engine import RunOutcome

    finding = Finding(1, "Low", "Finding", None, None, None, None, blocking)
    outcome = RunOutcome(1, RunState.WAITING_FOR_DECISION, findings=(finding,))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("fugue.cli.choices.LineChooser.choose", lambda *args, **kwargs: choice)
    monkeypatch.setattr("builtins.input", lambda message: answer)
    assert _prompt_decisions(outcome) == expected


def test_a_deleted_file_holds_no_conflict_marker(tmp_path: Path) -> None:
    from fugue.engine.parent import _has_conflict_markers

    marked = tmp_path / "a.txt"
    marked.write_text("<<<<<<< ours\nx\n=======\ny\n>>>>>>> theirs\n")
    assert _has_conflict_markers(marked)
    assert not _has_conflict_markers(tmp_path / "deleted.txt")

