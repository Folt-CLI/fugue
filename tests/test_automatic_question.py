"""An M Question answers itself: analysis, selection by policy, children without a prompt."""

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_analysis import handoff
from test_children import FakeGitHub
from test_children_propose import proposal
from test_engine_to_pr import result

from fugue.agents import AdapterRegistry, ScriptedAdapter, ScriptStep
from fugue.artifacts import ArtifactStore
from fugue.children.propose import ProposalStore
from fugue.config import FugueConfig
from fugue.engine import EngineDeps
from fugue.engine.facts import selected_solution
from fugue.github.attachments import UploadedAttachment
from fugue.models import IssueType, Priority, Size, Status, Ticket
from fugue.questions.automatic import answer_question, is_automatic
from fugue.store import Store
from fugue.workflow import RunState


def triggers(fired: str | None) -> list[dict[str, Any]]:
    return [
        {"name": n, "fired": n == fired, "evidence": "Seen." if n == fired else "None."}
        for n in ("ambiguity", "behaviour", "architecture", "irreversible")
    ]


class QuestionGitHub(FakeGitHub):
    """A Question that can be analysed, selected and given children."""

    def __init__(self, *, size: Size = Size.M) -> None:
        super().__init__()
        self.sections = {"Summary": "Which way?", "Scope": "Small", "Solution": "_Pending._"}
        self.size = size
        self.status = Status.TO_DO
        self.contents: dict[str, bytes] = {}
        self.posted: list[str] = []

    async def get_ticket(self, number: int) -> Ticket:
        return Ticket(
            number=10,
            title="Choose a feature",
            issue_type=IssueType.QUESTION,
            size=self.size,
            status=self.status,
            priority=Priority.MEDIUM,
            milestone="0.1.0",
            assignees=["developer"],
            body_sections=dict(self.sections),
        )

    async def children(self, number: int) -> list[Ticket]:
        return [
            Ticket(
                number=n,
                title=f"Child {n}",
                issue_type=IssueType.FEATURE,
                size=Size.XS,
                status=Status.TO_DO,
                priority=Priority.MEDIUM,
                parent=10,
            )
            for n in self.issues
        ]

    async def get_body(self, number: int) -> str:
        return "\n\n".join(f"## {k}\n\n{v}" for k, v in self.sections.items())

    async def edit_body(self, number: int, text: str) -> None:
        self.sections = {}
        for block in text.split("\n## "):
            name, _, rest = block.removeprefix("## ").partition("\n")
            self.sections[name] = rest.strip()

    async def comment(self, number: int, body: str) -> None:
        self.posted.append(body)

    async def set_status(self, number: int, status: Status) -> None:
        if number == 10:
            self.status = status
        else:
            await super().set_status(number, status)

    async def upload_attachments(self, paths: Sequence[Path]) -> list[UploadedAttachment]:
        out = []
        for path in paths:
            url = f"https://github.com/user-attachments/files/{len(self.contents) + 1}/{path.name}"
            self.contents[url] = path.read_bytes()
            out.append(
                UploadedAttachment(path.name, url, hashlib.sha256(path.read_bytes()).hexdigest())
            )
        return out

    async def download_attachment(self, url: str, dest: Path) -> Path:
        dest.write_bytes(self.contents[url])
        return dest


def deps_for(
    tmp_path: Path,
    github: QuestionGitHub,
    fired: str | None = None,
    children: dict[str, Any] | None = None,
) -> EngineDeps:
    analysis = handoff().model_dump(mode="json") | {"triggers": triggers(fired)}
    steps = [
        ScriptStep(result=replace(result(), provider="claude", structured_output=analysis)),
        ScriptStep(
            result=replace(result(), provider="claude", structured_output=children or proposal())
        ),
    ]
    adapter = ScriptedAdapter(steps, name="claude")
    registry = AdapterRegistry()
    registry.register("claude", lambda config: adapter)
    deps = EngineDeps(
        config=FugueConfig(),
        store=Store(tmp_path / "store.db"),
        artifacts=ArtifactStore(tmp_path / "artifacts"),
        github=github,
        registry=registry,
        repo_root=tmp_path,
        worktrees_root=tmp_path / "worktrees",
        state_dir=tmp_path / "state",
    )
    deps.adapter = adapter  # type: ignore[attr-defined]
    return deps


def test_only_a_question_whose_size_selects_by_policy_is_automatic() -> None:
    async def read(size: Size) -> Ticket:
        return await QuestionGitHub(size=size).get_ticket(10)

    policy = FugueConfig().policy
    assert is_automatic(asyncio.run(read(Size.M)), policy)
    assert not is_automatic(asyncio.run(read(Size.L)), policy)
    assert not is_automatic(
        asyncio.run(read(Size.S)).model_copy(update={"size": Size.M, "issue_type": IssueType.TASK}),
        policy,
    )


def test_an_m_question_selects_by_policy_and_creates_its_children(tmp_path: Path) -> None:
    github = QuestionGitHub()
    deps = deps_for(tmp_path, github)
    try:
        question = asyncio.run(github.get_ticket(10))
        outcome = asyncio.run(answer_question(deps, question))
        assert outcome.problems == ()
        assert outcome.state == RunState.RUNNING
        assert outcome.notes == ("Created children #20",)
        assert selected_solution(asyncio.run(github.get_ticket(10)))
        assert github.status == Status.READY_FOR_IMPLEMENTATION
        assert "by policy: no trigger fired" in github.posted[0]
        assert list(github.issues) == [20]
        assert "## Solution\n\n_Not applicable._" in github.issues[20]
        assert len(deps.adapter.requests) == 2  # type: ignore[attr-defined]
        again = asyncio.run(answer_question(deps, asyncio.run(github.get_ticket(10))))
        assert again.state == RunState.RUNNING and again.notes == ("Children already created",)
        assert list(github.issues) == [20] and len(deps.adapter.requests) == 2  # type: ignore[attr-defined]
    finally:
        deps.store.close()


def test_a_fired_trigger_parks_the_question_and_creates_nothing(tmp_path: Path) -> None:
    github = QuestionGitHub()
    deps = deps_for(tmp_path, github, fired="architecture")
    try:
        outcome = asyncio.run(answer_question(deps, asyncio.run(github.get_ticket(10))))
        assert outcome.state == RunState.WAITING_FOR_SELECTION
        assert github.issues == {} and github.posted == []
        assert not selected_solution(asyncio.run(github.get_ticket(10)))
    finally:
        deps.store.close()


def test_an_invalid_proposal_blocks_and_stays_editable(tmp_path: Path) -> None:
    github = QuestionGitHub()
    deps = deps_for(tmp_path, github, children=proposal(components=[]))
    try:
        outcome = asyncio.run(answer_question(deps, asyncio.run(github.get_ticket(10))))
        assert outcome.state == RunState.BLOCKED
        assert outcome.problems[0].startswith("Child proposal:")
        assert github.issues == {}
        saved = ProposalStore(tmp_path / "state").load(github.repo, 10)
        assert saved is not None and saved["locked"] is False
    finally:
        deps.store.close()


def test_a_question_that_is_not_automatic_is_refused(tmp_path: Path) -> None:
    github = QuestionGitHub(size=Size.L)
    deps = deps_for(tmp_path, github)
    try:
        with pytest.raises(ValueError, match="not an M Question"):
            asyncio.run(answer_question(deps, asyncio.run(github.get_ticket(10))))
    finally:
        deps.store.close()


def engine_for(github: QuestionGitHub) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(deps=SimpleNamespace(config=FugueConfig(), github=github))


@pytest.mark.parametrize(
    ("size", "state", "stops"),
    [
        (Size.M, RunState.RUNNING, False),
        (Size.M, RunState.WAITING_FOR_SELECTION, True),
        (Size.L, RunState.RUNNING, False),
    ],
)
def test_run_answers_an_m_question_first(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    size: Size,
    state: RunState,
    stops: bool,
) -> None:
    import fugue.cli.app as app
    from fugue.engine import RunOutcome

    asked: list[int] = []

    async def fake_answer(deps: Any, ticket: Ticket) -> RunOutcome:
        asked.append(ticket.number)
        return RunOutcome(1, state, notes=("Created children #20",))

    monkeypatch.setattr(app, "answer_question", fake_answer)
    outcome = asyncio.run(app._answer_automatically(engine_for(QuestionGitHub(size=size)), 10))
    if size == Size.L:
        assert asked == [] and outcome is None
    else:
        assert asked == [10]
        assert (outcome is not None) == stops
        assert "note: Created children #20" in capsys.readouterr().out


def test_children_made_by_hand_are_not_proposed_again(tmp_path: Path) -> None:
    github = QuestionGitHub()
    github.issues[30] = "Made by hand"
    deps = deps_for(tmp_path, github)
    try:
        outcome = asyncio.run(answer_question(deps, asyncio.run(github.get_ticket(10))))
        assert outcome.state == RunState.RUNNING and outcome.notes == ("Children already exist",)
        assert list(github.issues) == [30]
        assert len(deps.adapter.requests) == 1  # type: ignore[attr-defined]  # analysis only
    finally:
        deps.store.close()


def parent_run(deps: EngineDeps, question: Ticket) -> Any:
    from fugue.engine.parent import _ParentRun, plan_children

    parent = deps.store.create_parent_run(5, deps.github.repo)
    deps.store.add_work(parent.id, question.number, 0)
    return _ParentRun(deps, parent, plan_children([question]), [question], False, "")


def test_a_parent_run_takes_the_children_of_an_m_question_into_its_work(tmp_path: Path) -> None:
    github = QuestionGitHub()
    deps = deps_for(tmp_path, github)
    try:
        question = asyncio.run(github.get_ticket(10))
        runner = parent_run(deps, question)
        asyncio.run(runner._answer_questions())
        assert sorted(runner.tickets) == [10, 20]
        assert runner.plan.order == (10, 20)
        assert [i.ticket_number for i in deps.store.list_work(runner.parent.id)] == [10, 20]
        assert [number for number, _ in runner.outcomes] == [10]
        assert selected_solution(asyncio.run(github.get_ticket(10)))
        asyncio.run(runner._answer_questions())
        assert list(github.issues) == [20] and len(deps.adapter.requests) == 2  # type: ignore[attr-defined]
    finally:
        deps.store.close()


def test_a_parked_question_adds_no_children_to_the_parent_run(tmp_path: Path) -> None:
    github = QuestionGitHub()
    deps = deps_for(tmp_path, github, fired="irreversible")
    try:
        runner = parent_run(deps, asyncio.run(github.get_ticket(10)))
        asyncio.run(runner._answer_questions())
        assert sorted(runner.tickets) == [10]
        assert runner.outcomes[0][1].state == RunState.WAITING_FOR_SELECTION
        assert github.issues == {}
    finally:
        deps.store.close()


class BlockedQuestionGitHub(QuestionGitHub):
    """The Question waits for #99, a ticket outside the parent; its children are done."""

    def __init__(self) -> None:
        super().__init__()
        self.blocker = Status.TO_DO

    async def get_ticket(self, number: int) -> Ticket:
        if number == 99:
            return Ticket(
                number=99,
                title="Outside work",
                issue_type=IssueType.TASK,
                size=Size.S,
                status=self.blocker,
                priority=Priority.MEDIUM,
            )
        question = await super().get_ticket(number)
        return question.model_copy(update={"blocked_by": [99]})

    async def children(self, number: int) -> list[Ticket]:
        return [
            child.model_copy(update={"status": Status.DONE})
            for child in await super().children(number)
        ]


def test_a_question_with_an_open_blocker_is_answered_only_once_it_closes(tmp_path: Path) -> None:
    github = BlockedQuestionGitHub()
    deps = deps_for(tmp_path, github)
    try:
        runner = parent_run(deps, asyncio.run(github.get_ticket(10)))
        waiting = asyncio.run(runner._run_children())
        assert waiting[10].state == RunState.BLOCKED and waiting[10].waits_for == (99,)
        assert deps.adapter.requests == []  # type: ignore[attr-defined]
        assert not selected_solution(asyncio.run(github.get_ticket(10)))
        github.blocker = Status.DONE
        answered = asyncio.run(runner._run_children())
        assert answered[10].state == "answered"
        assert len(deps.adapter.requests) == 2  # type: ignore[attr-defined]
        assert selected_solution(asyncio.run(github.get_ticket(10)))
        assert list(github.issues) == [20]
    finally:
        deps.store.close()
