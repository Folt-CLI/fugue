"""Child plans validate before mutations and resume partial external work."""

import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fugue.children import ChildrenSpec, ChildSpec, apply, load_children, plan
from fugue.children.apply import ChildrenPlan, render_body
from fugue.cli.app import app
from fugue.config import RepoPolicy
from fugue.github import GitHubClient
from fugue.github.attachments import UploadedAttachment
from fugue.github.parse import parse_body_sections
from fugue.models import Component, IssueType, Priority, Size, Status, Ticket
from fugue.process import CommandResult
from fugue.store import Store


def child(**updates: Any) -> ChildSpec:
    values: dict[str, Any] = {
        "key": "one",
        "title": "Add a child",
        "size": "XS",
        "components": ["Source Code"],
        "summary": "Expected result",
        "scope": "**In scope**\n\n- Feature\n\n**Out of scope**\n\n- Other",
        "decision": "Implement the decision",
        "agreed": ["Selected feature"],
        "you_get": ["A feature"],
        "changes": ["Implement it"],
        "not_changed": ["Other work"],
        "watch_out": ["Requires configuration"],
    }
    return ChildSpec.model_validate({**values, **updates})


def specification(*children: ChildSpec) -> ChildrenSpec:
    return ChildrenSpec(
        question=10, milestone="0.1.0", assignee="developer", children=list(children)
    )


def question(**updates: Any) -> Ticket:
    return Ticket.model_validate(
        {
            "number": 10,
            "title": "Choose a feature",
            "issue_type": "Question",
            "size": "L",
            "status": "S3: Ready For Implementation",
            "priority": "Medium",
            "body_sections": {
                "Solution": "[Selected Solution] [10-solution-1.md](https://github.com/user-attachments/a)"
            },
            **updates,
        }
    )


def planned(*children: ChildSpec) -> ChildrenPlan:
    return plan(specification(*children), question(), RepoPolicy())


class FakeGitHub(GitHubClient):
    def __init__(self) -> None:
        super().__init__("example/repo", "example", 2)
        self.calls: list[tuple[str, int]] = []
        self.issues: dict[int, str] = {}
        self.fail_create = False
        self.fail_priority = False

    async def find_child(self, parent: int, marker: str) -> int | None:
        return next((n for n, body in self.issues.items() if marker in body), None)

    async def create_issue(
        self,
        title: str,
        body: str,
        issue_type: IssueType,
        parent: int,
        milestone: str,
        assignee: str,
        *,
        add_to_project: bool = True,
    ) -> int:
        assert parent == 10 and milestone == "0.1.0" and assignee == "developer"
        number = 20 + len(self.issues)
        self.issues[number] = body
        self.calls.append(("create", number))
        if self.fail_create:
            self.fail_create = False
            raise RuntimeError("response lost")
        return number

    async def ensure_project(self, number: int) -> None:
        self.calls.append(("project", number))

    async def set_size(self, number: int, size: Size) -> None:
        self.calls.append(("size", number))

    async def set_status(self, number: int, status: Status) -> None:
        assert status == Status.TO_DO
        self.calls.append(("status", number))

    async def set_priority(self, number: int, priority: Priority) -> None:
        if self.fail_priority:
            self.fail_priority = False
            raise RuntimeError("priority unavailable")
        self.calls.append(("priority", number))

    async def set_components(self, number: int, components: Sequence[Component]) -> None:
        self.calls.append(("components", number))

    async def upload_attachments(self, paths: Sequence[Path]) -> list[UploadedAttachment]:
        assert paths[0].read_text() == "Implementation scope"
        self.calls.append(("upload", 20))
        return [
            UploadedAttachment(paths[0].name, "https://github.com/user-attachments/scope", "sha")
        ]

    async def edit_issue_body(self, number: int, body: str) -> None:
        self.calls.append(("body", number))
        self.issues[number] = body

    async def block(self, number: int, blocker: int) -> None:
        assert blocker in self.issues or blocker == 7
        self.calls.append(("block", number))


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"size": "S"}, "scope attachment"),
        ({"components": []}, "assigned Components"),
        ({"components": ["Build", "Tests"]}, "at most 1"),
        ({"size": "M", "components": ["Build", "Tests", "Source Code"]}, "at most 2"),
    ],
)
def test_invalid_child_fails_before_apply(updates: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        planned(child(**updates))


@pytest.mark.parametrize("blockers", [["unknown"], ["one"], [0], [-1]])
def test_invalid_blockers(blockers: list[str | int]) -> None:
    with pytest.raises(ValueError):
        specification(child(blocked_by=blockers))


def test_cycles_and_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="cycle"):
        specification(child(blocked_by=["two"]), child(key="two", blocked_by=["one"]))
    with pytest.raises(ValueError, match="unique"):
        specification(child(), child())


@pytest.mark.parametrize("updates", [{"issue_type": "Task"}, {"body_sections": {}}, {"number": 9}])
def test_requires_decided_question(updates: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="Question"):
        plan(specification(child()), question(**updates), RepoPolicy())


def test_dry_run_no_side_effects() -> None:
    client, store = FakeGitHub(), Store(":memory:")
    try:
        assert asyncio.run(apply(planned(child()), client, store, dry_run=True)) == {}
        assert client.calls == []
        assert store.list_runs() == []
    finally:
        store.close()


def test_apply_order_and_idempotent_rerun(tmp_path: Path) -> None:
    scope = tmp_path / "scope.md"
    scope.write_text("Implementation scope")
    saved_plan = planned(child(size="S", scope_file=scope, blocked_by=["two", 7]), child(key="two"))
    client = FakeGitHub()
    database = tmp_path / "store.db"
    with_store = Store(database)
    assert asyncio.run(apply(saved_plan, client, with_store)) == {"one": 20, "two": 21}
    with_store.close()
    calls = client.calls.copy()
    assert calls[:8] == [
        (name, 20)
        for name in (
            "create",
            "project",
            "size",
            "status",
            "priority",
            "components",
            "upload",
            "body",
        )
    ]
    assert calls[-2:] == [("block", 20), ("block", 20)]
    assert calls.index(("create", 21)) < calls.index(("block", 20))
    assert "[20-solution-1.md](https://github.com/user-attachments/scope)" in client.issues[20]
    assert set(parse_body_sections(client.issues[20])) == {
        "Summary",
        "Scope",
        "Analysis",
        "Solution",
        "Details",
    }
    reopened = Store(database)
    try:
        assert asyncio.run(apply(saved_plan, client, reopened)) == {"one": 20, "two": 21}
        assert client.calls == calls
        assert reopened.pending_operations(reopened.list_runs()[0].id) == []
    finally:
        reopened.close()


@pytest.mark.parametrize("failure", ["fail_create", "fail_priority"])
def test_resume_after_partial_failure(failure: str) -> None:
    client, store = FakeGitHub(), Store(":memory:")
    setattr(client, failure, True)
    saved_plan = planned(child())
    try:
        with pytest.raises(RuntimeError):
            asyncio.run(apply(saved_plan, client, store))
        assert asyncio.run(apply(saved_plan, client, store)) == {"one": 20}
        assert client.calls.count(("create", 20)) == 1
        assert client.calls.count(("project", 20)) == 1
        assert store.pending_operations(store.list_runs()[0].id) == []
    finally:
        store.close()


def test_changed_plan_refused() -> None:
    client, store = FakeGitHub(), Store(":memory:")
    try:
        asyncio.run(apply(planned(child()), client, store))
        calls = client.calls.copy()
        with pytest.raises(ValueError, match="differs"):
            asyncio.run(apply(planned(child(title="Different")), client, store))
        assert calls == client.calls
    finally:
        store.close()


def test_toml_loader_resolves_relative_scope_and_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "children.toml"
    data = child(size="S", scope_file=Path("scope.md")).model_dump(mode="json")
    header = 'question = 10\nmilestone = "0.1.0"\nassignee = "developer"\n[[children]]\n'
    path.write_text(
        header + "\n".join(f"{key} = {json.dumps(value)}" for key, value in data.items())
    )
    assert load_children(path).children[0].scope_file == tmp_path / "scope.md"
    path.write_text(path.read_text() + '\nunknown = "no"')
    with pytest.raises(ValueError, match="unknown"):
        load_children(path)


def test_missing_and_empty_scope_files(tmp_path: Path) -> None:
    path = tmp_path / "scope.md"
    with pytest.raises(OSError):
        planned(child(size="S", scope_file=path))
    path.write_text(" ")
    with pytest.raises(ValueError, match="non-empty"):
        planned(child(size="S", scope_file=path))


def test_m_child_starts_pending() -> None:
    body = parse_body_sections(render_body(child(size="M"), 10))
    assert body["Analysis"] == body["Solution"] == "_Pending._"


def test_cli_registered_and_missing_spec_is_usage_error(tmp_path: Path) -> None:
    runner = CliRunner()
    assert "--apply" in runner.invoke(app, ["children", "--help"]).output
    result = runner.invoke(
        app, ["children", str(tmp_path / "missing"), "--repo-root", str(tmp_path)]
    )
    assert result.exit_code == 2
    assert "error:" in result.output


def test_github_creation_and_native_block_relationship() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        stdin: str | None = None,
        max_output_bytes: int = 65536,
    ) -> CommandResult:
        args = list(argv)[1:]
        calls.append((args, stdin))
        output = ""
        if args[:2] == ["issue", "create"]:
            output = "https://github.com/example/repo/issues/20\n"
        elif args[:2] == ["api", "graphql"]:
            output = json.dumps(
                [{"data": {"repository": {"issue": {"projectItems": {"nodes": []}}}}}]
            )
        elif args[:2] == ["api", "repos/example/repo/issues/7"]:
            output = '{"id": 700}'
        elif "--paginate" in args:
            output = "[[]]"
        return CommandResult(tuple(argv), 0, False, False, output, "", False, False, False, None, 0)

    client = GitHubClient("example/repo", "example", 2, runner=runner, strip_tokens=False)
    assert (
        asyncio.run(client.create_issue("Title", "Body", IssueType.FEATURE, 10, "0.1.0", "dev"))
        == 20
    )
    assert calls[0][1] == "Body"
    assert calls[0][0] == [
        "issue",
        "create",
        "--repo",
        "example/repo",
        "--title",
        "Title",
        "--body-file",
        "-",
        "--type",
        "Feature",
        "--parent",
        "10",
        "--milestone",
        "0.1.0",
        "--assignee",
        "dev",
    ]
    assert calls[-1][0][:2] == ["project", "item-add"]
    asyncio.run(client.block(20, 7))
    assert calls[-1][0] == [
        "api",
        "-X",
        "POST",
        "repos/example/repo/issues/20/dependencies/blocked_by",
        "-F",
        "issue_id=700",
    ]


def test_upload_failure_keeps_creation_for_retry(tmp_path: Path) -> None:
    class FailingUpload(FakeGitHub):
        fail_upload = True

        async def upload_attachments(self, paths: Sequence[Path]) -> list[UploadedAttachment]:
            if self.fail_upload:
                self.fail_upload = False
                raise RuntimeError("verification failed")
            return await super().upload_attachments(paths)

    path = tmp_path / "scope.md"
    path.write_text("Implementation scope")
    client, store = FailingUpload(), Store(":memory:")
    saved_plan = planned(child(size="S", scope_file=path))
    try:
        with pytest.raises(RuntimeError, match="verification failed"):
            asyncio.run(apply(saved_plan, client, store))
        assert ("body", 20) not in client.calls
        assert asyncio.run(apply(saved_plan, client, store)) == {"one": 20}
        assert client.calls.count(("create", 20)) == 1
        assert client.calls[-1] == ("body", 20)
    finally:
        store.close()


def test_cli_dry_run_does_not_create_store_or_call_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    cli = importlib.import_module("fugue.cli.app")

    class Client(FakeGitHub):
        def __init__(self, *args: object) -> None:
            super().__init__()

        async def get_ticket(self, number: int) -> Ticket:
            return question()

        async def find_child(self, parent: int, marker: str) -> int | None:
            pytest.fail("Dry run must not begin applying the plan")

    spec = tmp_path / "children.toml"
    spec.write_text('question = 10\nmilestone = "0.1.0"\nassignee = "developer"')
    config = tmp_path / "fugue.toml"
    state_dir = tmp_path / "state"
    config.write_text(
        f"state_dir = {json.dumps(str(state_dir))}\n"
        '[github]\nrepo = "example/repo"\nproject_owner = "example"\nproject_number = 2\n'
    )
    monkeypatch.setattr(cli, "GitHubClient", Client)
    monkeypatch.setattr("fugue.children.load_children", lambda path: specification(child()))
    result = CliRunner().invoke(
        app, ["children", str(spec), "--config", str(config), "--repo-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "one: Add a child (XS)" in result.output
    assert not state_dir.exists()
