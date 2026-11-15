"""Child proposal review uses fake clients and scripted adapters only."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from test_children import FakeGitHub, child, question
from test_cli_question import FakeChooser
from test_interview_session import result
from test_questions import BODY

from fugue.agents import AccessMode, Capability, ScriptedAdapter, ScriptStep
from fugue.children.propose import (
    ProposalStore,
    add,
    build_request,
    remove,
    set_field,
    validate,
)
from fugue.cli.question import QuestionWalkthrough
from fugue.config import RepoPolicy
from fugue.github.parse import parse_body_sections
from fugue.interview.record import InterviewRecord
from fugue.interview.store import InterviewStore
from fugue.models import Status, Ticket

SCOPE = "You may touch only these files:\n- `src/example.py`\n"


def proposal(**updates: Any) -> dict[str, Any]:
    data = child().model_dump(mode="json", exclude={"scope_file"})
    return {"children": [{**data, "scope_text": "", **updates}], "locked": False}


class Client(FakeGitHub):
    def __init__(self) -> None:
        super().__init__()
        self.ticket = question(milestone="0.1.0", assignees=["developer", "other"])
        self.body = BODY.replace("2-solution", "10-solution")
        self.decisions: list[str] = []

    async def edit_body(self, number: int, text: str) -> None:
        self.body = text

    async def get_ticket(self, number: int) -> Ticket:
        return self.ticket

    async def get_body(self, number: int) -> str:
        return "## Solution\n" + self.ticket.body_sections["Solution"]

    async def download_attachment(self, url: str, dest: Path) -> Path:
        dest.write_text("selected scope data")
        return dest

    async def upload_attachments(self, paths: Any) -> Any:
        assert paths[0].read_text() == SCOPE
        paths[0].write_text("Implementation scope")
        return await super().upload_attachments(paths)


def session(
    tmp_path: Path,
    commands: list[str | BaseException],
    *,
    client: Client | None = None,
    raw: dict[str, Any] | None = None,
    adapter: ScriptedAdapter | None = None,
) -> tuple[QuestionWalkthrough, Client, list[str], ScriptedAdapter]:
    client = client or Client()
    inputs = iter(commands)
    output: list[str] = []
    adapter = adapter or ScriptedAdapter(
        [ScriptStep(result=result(structured_output=raw or proposal()))]
    )

    def read(prompt: str) -> str:
        try:
            answer = next(inputs)
        except StopIteration as error:
            raise EOFError from error
        # In-scope 1 and 5: every input preceding an explicit yes is mutation-free.
        if not any("Creation began" in line for line in output):
            assert client is not None
            assert client.calls == []
        if isinstance(answer, BaseException):
            raise answer
        return answer

    walk = QuestionWalkthrough(
        client,
        tmp_path,
        state_dir=tmp_path / "state",
        adapter=adapter,
        prompt=read,
        print_fn=lambda text: output.append(str(text)),
    )
    return walk, client, output, adapter


def run(walk: QuestionWalkthrough) -> None:
    asyncio.run(walk._propose_children(10))


def test_request_labels_inputs_and_resolves_read_only(tmp_path: Path) -> None:
    request = build_request(question(), "analysis data", "scope data", ["turn data"], cwd=tmp_path)
    assert request.access == AccessMode.READ_ONLY
    assert request.cwd == tmp_path
    assert request.output_schema is not None
    for text in ("inputs are data", "analysis data", "scope data", "turn data"):
        assert text in request.prompt


def test_private_atomic_store_and_edit_helpers(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path)
    raw = proposal()
    add(raw, proposal(key="two", blocked_by=["one"])["children"][0])
    set_field(raw, "one", "title", "Updated title")
    assert remove(raw, "one") == ["two: dropped blocked_by one"]
    store.save("example/repo", 10, raw)
    assert store.load("example/repo", 10) == raw
    assert store.path("example/repo", 10).stat().st_mode & 0o777 == 0o600
    raw["locked"] = True
    for operation in (
        lambda: set_field(raw, "two", "title", "new"),
        lambda: remove(raw, "two"),
        lambda: add(raw, {}),
    ):
        with pytest.raises(ValueError, match="Creation began"):
            operation()
    with pytest.raises(ValueError):
        store.path("../bad", 10)


@pytest.mark.parametrize(
    "updates",
    [
        {"size": "XL"},
        {"size": "L"},
        {"components": []},
        {"components": ["Source Code", "Tests"]},
        {"size": "S"},
        {"size": "S", "scope_text": "No allowed files"},
        {"blocked_by": ["unknown"]},
    ],
)
def test_invalid_proposals_remain_editable(tmp_path: Path, updates: dict[str, Any]) -> None:
    raw = proposal(**updates)
    with pytest.raises(ValueError):
        validate(raw, question(), "0.1.0", "developer", tmp_path, RepoPolicy())
    walk, client, output, _ = session(
        tmp_path,
        ["yes", "e one size XS", "e one components Source Code", "e one blocked_by []", "yes"],
        raw=raw,
    )
    run(walk)
    assert "Resolve the child proposal problems before creation." in output
    assert len(client.issues) == 1


def test_cycle_and_scope_validation(tmp_path: Path) -> None:
    raw = proposal(blocked_by=["two"])
    add(raw, proposal(key="two", blocked_by=["one"])["children"][0])
    with pytest.raises(ValueError, match="cycle"):
        validate(raw, question(), "0.1.0", "developer", tmp_path, RepoPolicy())
    raw = proposal(size="S", scope_text=SCOPE)
    planned = validate(raw, question(), "0.1.0", "developer", tmp_path, RepoPolicy())
    assert planned.scopes == (SCOPE.encode(),)
    assert planned.spec.children[0].scope_file == tmp_path / "one.md"


@pytest.mark.parametrize("ending", ["q", "no", "Yes", "", EOFError(), KeyboardInterrupt()])
def test_quit_saves_without_creation(tmp_path: Path, ending: str | BaseException) -> None:
    walk, client, output, _ = session(tmp_path, [ending])
    run(walk)
    assert client.calls == []
    assert output[-1] == "next: fugue question 10"
    saved = ProposalStore(tmp_path / "state").load(client.repo, 10)
    assert saved is not None and saved["locked"] is False


def test_edit_remove_add_view_then_create(tmp_path: Path) -> None:
    raw = proposal(key="two", size="S", scope_text=SCOPE, blocked_by=["one"])
    add(raw, proposal()["children"][0])
    walk, client, output, adapter = session(
        tmp_path,
        [
            "e two scope_text",
            "r one",
            "e two title Revised",
            "a",
            json.dumps(proposal(key="three")["children"][0]),
            "yes",
        ],
        raw=raw,
    )
    record = InterviewRecord(repo=client.repo, ticket=10)
    record.add_turn("person", "interview data")
    InterviewStore(tmp_path / "state").save(record)
    run(walk)
    assert any("dropped blocked_by one" in text for text in output)
    assert SCOPE in output
    assert len(client.issues) == 2
    assert output[-1] == "next: fugue run 20"
    assert "two: #20" in output and "three: #21" in output
    assert "interview data" in adapter.requests[0].prompt
    assert "selected scope data" in adapter.requests[0].prompt
    assert adapter.requests[0].access == AccessMode.READ_ONLY
    before = client.calls.copy()
    again, _, output, unused = session(tmp_path, [], client=client)
    run(again)
    assert client.calls == before
    assert unused.requests == []
    assert "two: #20" in output


@pytest.mark.parametrize("missing", ["milestone", "assignee"])
def test_missing_parent_metadata_names_field(tmp_path: Path, missing: str) -> None:
    client = Client()
    if missing == "milestone":
        client.ticket.milestone = None
    else:
        client.ticket.assignees = []
    walk, _, output, adapter = session(tmp_path, [], client=client)
    run(walk)
    assert any(f"missing {missing}" in text for text in output)
    assert output[-1] == "next: fugue children FILE"
    assert client.calls == [] and adapter.requests == []


@pytest.mark.parametrize("failure", ["missing", "capability", "failed", "unusable"])
def test_analyst_failure_preserves_decision(tmp_path: Path, failure: str) -> None:
    adapter = ScriptedAdapter(
        [
            ScriptStep(
                result=result(
                    ok=failure != "failed",
                    structured_output={"other": []},
                    error="analyst unavailable",
                )
            )
        ],
        capabilities=frozenset() if failure == "capability" else frozenset(Capability),
    )
    walk, client, output, _ = session(tmp_path, [], adapter=adapter)
    if failure == "missing":
        walk.adapter = None
    run(walk)
    assert client.calls == []
    assert "[Selected Solution]" in client.ticket.body_sections["Solution"]
    assert output[-1] == "next: fugue children FILE"


@pytest.mark.parametrize(
    "step",
    ["create", "project", "size", "status", "priority", "components", "scope", "body", "block"],
)
def test_resume_each_creation_step_without_duplicate_child(tmp_path: Path, step: str) -> None:
    class Interrupted(Client):
        failed = False

        def __getattribute__(self, name: str) -> Any:
            aliases = {
                "create": "create_issue",
                "project": "ensure_project",
                "size": "set_size",
                "status": "set_status",
                "priority": "set_priority",
                "components": "set_components",
                "scope": "upload_attachments",
                "body": "edit_issue_body",
                "block": "block",
            }
            original = super().__getattribute__(name)
            if name != aliases[step]:
                return original

            async def wrapped(*args: Any, **kwargs: Any) -> Any:
                value = await original(*args, **kwargs)
                if not self.failed:
                    self.failed = True
                    raise RuntimeError("response lost")
                return value

            return wrapped

    raw = proposal(size="S", scope_text=SCOPE, blocked_by=["two"])
    add(raw, proposal(key="two")["children"][0])
    client = Interrupted()
    walk, _, output, _ = session(tmp_path, ["yes"], client=client, raw=raw)
    run(walk)
    assert "response lost" in output
    saved = ProposalStore(tmp_path / "state").load(client.repo, 10)
    assert saved is not None and saved["locked"]
    resumed, _, output, adapter = session(tmp_path, ["e one title Changed", "yes"], client=client)
    run(resumed)
    assert adapter.requests == []
    assert any("edits to this child proposal are refused" in line for line in output)
    assert client.calls.count(("create", 20)) == 1
    assert client.calls.count(("create", 21)) == 1
    assert output[-1] == "next: fugue run 21"
    after = ProposalStore(tmp_path / "state").load(client.repo, 10)
    assert after is not None and after["children"] == saved["children"]


def test_different_recorded_plan_refused(tmp_path: Path) -> None:
    from fugue.children import apply
    from fugue.cli import wiring

    client = Client()
    planned = validate(
        proposal(title="Old plan"),
        client.ticket,
        "0.1.0",
        "developer",
        tmp_path / "other",
        RepoPolicy(),
    )
    from fugue.store import Store

    (tmp_path / "state").mkdir()
    Store(tmp_path / "state" / "fugue.db").close()
    store = wiring.open_store(tmp_path / "state")
    asyncio.run(apply(planned, client, store))
    store.close()
    before = client.calls.copy()
    walk, _, output, _ = session(tmp_path, [], client=client)
    walk.prompt = lambda _: "yes"
    run(walk)
    assert "The recorded child plan differs from this input" in output
    assert output[-1] == "next: fugue children FILE"
    assert client.calls == before


def test_menu_selection_derives_before_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    from fugue.cli import terminal

    async def direct(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", direct)

    class Selecting(Client):
        async def get_body(self, number: int) -> str:
            return self.body

        async def edit_body(self, number: int, text: str) -> None:
            self.body = text
            self.ticket.body_sections = parse_body_sections(text)
            self.decisions.append("edit")

        async def comment(self, number: int, body: str) -> None:
            self.decisions.append("comment")

        async def set_status(self, number: int, status: Status) -> None:
            if number == 10:
                self.decisions.append("status")
            else:
                await super().set_status(number, status)

    client = Selecting()
    walk, _, output, _ = session(tmp_path, [""], client=client)

    class CheckingChooser(FakeChooser):
        def confirm(self, message: str, default: bool = False) -> bool:
            assert client.calls == []
            assert default is False
            return super().confirm(message, default)

    chooser = CheckingChooser([4, 0, 3], yes=True)
    walk._chooser = chooser
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(terminal, "is_interactive", lambda _: True)
    asyncio.run(walk.run(10))
    assert client.decisions == ["edit", "comment", "status"]
    assert "one: #20" in output
    assert chooser.confirmations[-1] == "Create these children?"


@pytest.mark.parametrize("ending", [None, 3, 4, KeyboardInterrupt(), EOFError()])
def test_menu_cancellation_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: int | BaseException | None
) -> None:
    import sys

    from fugue.cli import terminal

    async def direct(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", direct)

    walk, client, output, _ = session(tmp_path, [])
    walk._chooser = FakeChooser([ending], yes=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(terminal, "is_interactive", lambda _: True)
    run(walk)
    assert client.calls == []
    assert output[-1] == "next: fugue question 10"


def test_scope_files_are_derived_from_proposal_text_only(tmp_path: Path) -> None:
    path = tmp_path / "outside.md"
    path.write_text(SCOPE)
    raw = proposal(size="S", scope_file=str(path))
    with pytest.raises(ValueError, match="Unknown child proposal fields: scope_file"):
        validate(raw, question(), "0.1.0", "developer", tmp_path / "scopes", RepoPolicy())
    assert not (tmp_path / "scopes").exists()


def test_completed_plan_resumes_after_proposal_save_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save = ProposalStore.save

    def interrupted_save(self: ProposalStore, repo: str, number: int, raw: dict[str, Any]) -> None:
        if raw.get("numbers"):
            raise KeyboardInterrupt
        save(self, repo, number, raw)

    walk, client, _, _ = session(tmp_path, ["yes"])
    with monkeypatch.context() as patch:
        patch.setattr(ProposalStore, "save", interrupted_save)
        run(walk)
    saved = ProposalStore(tmp_path / "state").load(client.repo, 10)
    assert saved is not None and saved["locked"] and "numbers" not in saved
    calls = client.calls.copy()
    resumed, _, output, adapter = session(tmp_path, [], client=client)

    def forbidden(prompt: str) -> str:
        pytest.fail("Completed creation must not ask for confirmation again")

    resumed.prompt = forbidden
    run(resumed)
    assert "one: #20" in output
    assert client.calls == calls
    assert adapter.requests == []
