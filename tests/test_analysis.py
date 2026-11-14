"""Analysis handoff, exact Markdown and restart behaviour without external calls."""

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_engine_to_pr import FakeGitHub

from fugue.agents import AccessMode, AdapterRegistry, AgentResult, ScriptedAdapter, ScriptStep
from fugue.analysis import Handoff, analyse, handoff_schema
from fugue.analysis.render import (
    render_analysis,
    render_attachments,
    render_body,
    render_details,
    render_scope,
)
from fugue.artifacts import ArtifactStore
from fugue.cli.app import _StoreClosingEngine
from fugue.config import FugueConfig
from fugue.engine import Engine, EngineDeps
from fugue.engine.facts import allowed_files
from fugue.github.attachments import UploadedAttachment
from fugue.models import AuthenticationMode, IssueType, Priority, Role, Size, Status, Ticket
from fugue.store import Store
from fugue.workflow import RunState


def handoff() -> Handoff:
    return Handoff.model_validate(
        {
            "decision": "Choose a scope.",
            "where_we_are": ["Analysis is pending."],
            "out_of_scope": ["Merging."],
            "terms": ["stage: a named step of a run."],
            "comparison": [],
            "options": [
                {
                    "name": "Publish analysis",
                    "you_get": ["Verified scopes."],
                    "changes": ["Add a stage."],
                    "watch_out": ["Selection remains explicit."],
                    "scope": {
                        "standing_rules": ["Implement only."],
                        "source_of_truth": ["GLOSSARY.md."],
                        "allowed_files": ["src/fugue/analysis/stage.py"],
                        "file_changes": ["Update stage.py."],
                        "in_scope": ["Restart reuses uploads."],
                        "out_of_scope": ["Merge approval."],
                        "do_not_change": ["Dependencies."],
                        "verification": ["Tests pass."],
                        "final_message": ["Report changes."],
                        "orchestrator_notes": ["Review boundaries."],
                    },
                }
            ],
            "recommendation": 1,
            "reason": "It provides verified scopes.",
            "findings": "# Findings\n\nstage.py:1 needs analysis.\n",
            "key_points": ["Analysis is pending.", "Selection remains explicit."],
        }
    )


def test_schema_round_trip() -> None:
    value = handoff()
    assert Handoff.model_validate_json(value.model_dump_json()) == value
    assert handoff_schema()["additionalProperties"] is False
    assert handoff_schema()["$defs"]["Scope"]["additionalProperties"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"unexpected": True},
        {"options": []},
        {"decision": " "},
        {"recommendation": 0},
        {"recommendation": 2},
        {"key_points": ["Only one"]},
        {"comparison": [{"label": "Risk", "values": ["a", "b"]}]},
    ],
)
def test_schema_rejects_invalid_handoffs(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Handoff.model_validate(handoff().model_dump() | change)


LINKS = {
    "43-code-findings.md": "https://github.com/user-attachments/files/1/findings.md",
    "43-solution-1.md": "https://github.com/user-attachments/files/2/scope.md",
}


def test_analysis_exact_text() -> None:
    assert (
        render_analysis(43, handoff(), LINKS)
        == """## Analysis

> [!IMPORTANT]
> Choose a scope.

### Where we are

- Analysis is pending.

**Out of scope**

- Merging.

**Terms**

- stage: a named step of a run.

### Option 1: Publish analysis

**You get**

- Verified scopes.

**Changes**

- Add a stage.

**Watch out**

- Selection remains explicit.

**Implementation scope:** [43-solution-1.md](https://github.com/user-attachments/files/2/scope.md)

### Recommendation

Option 1: It provides verified scopes.

> [!TIP]
> Reply with the option number to select its implementation scope.
"""
    )


def test_details_exact_text() -> None:
    assert (
        render_details(43, handoff(), LINKS)
        == """## Details

**Code findings:** [43-code-findings.md](https://github.com/user-attachments/files/1/findings.md)

**Key points**
- Analysis is pending.
- Selection remains explicit.
"""
    )


def test_scope_and_findings_exact_text() -> None:
    expected = """# 43 solution 1: Publish analysis

## Standing rules

- Implement only.
- You may touch only these files (create them if new):
- `src/fugue/analysis/stage.py`

## Source of truth

- GLOSSARY.md.

## File-by-file changes

- Update stage.py.

## In-scope scenarios

1. Restart reuses uploads.

## Out-of-scope scenarios

1. Merge approval.

## Do not change

- Dependencies.

## Verification

- Tests pass.

## Final message

- Report changes.

## Notes for the orchestrator (not for the implementation agent)

- Review boundaries.
"""
    assert render_scope(43, 1, handoff().options[0]) == expected
    assert render_attachments(43, handoff()) == {
        "43-code-findings.md": "# Findings\n\nstage.py:1 needs analysis.\n",
        "43-solution-1.md": expected,
    }


def test_allowed_files_round_trip_and_numbering() -> None:
    data = handoff().model_dump()
    scope = data["options"][0]["scope"]
    scope["allowed_files"] = ["a.py", "`b/c.py`"]
    scope["in_scope"] = ["1. Foo", "Bar", "2) Baz"]
    text = render_scope(43, 1, Handoff.model_validate(data).options[0])
    assert allowed_files(text) == ["a.py", "b/c.py"]
    assert "1. Foo\n2. Bar\n3. Baz" in text
    assert "1. 1." not in text


def test_schema_requires_allowed_files() -> None:
    data = handoff().model_dump()
    del data["options"][0]["scope"]["allowed_files"]
    with pytest.raises(ValidationError):
        Handoff.model_validate(data)
    data["options"][0]["scope"]["allowed_files"] = []
    with pytest.raises(ValidationError):
        Handoff.model_validate(data)


def test_comparison_required_and_rendered() -> None:
    data = handoff().model_dump()
    data["options"] *= 2
    with pytest.raises(ValidationError, match="require a comparison"):
        Handoff.model_validate(data)
    data["comparison"] = [{"label": "Main risk", "values": ["a|b", "c\nd"]}]
    value = Handoff.model_validate(data)
    links = LINKS | {"43-solution-2.md": LINKS["43-solution-1.md"]}
    assert (
        "| Comparison | Option 1 | Option 2 |\n| --- | --- | --- |\n"
        "| Main risk | a\\|b | c<br>d |\n"
    ) in render_analysis(43, value, links)


def test_body_keeps_five_sections_and_pending_solution() -> None:
    assert render_body(
        {"Summary": "Need analysis", "Scope": "M/L", "Solution": "Pending"},
        "## Analysis\n\nOptions\n",
        "## Details\n\nFindings\n",
    ) == (
        "## Summary\n\nNeed analysis\n\n## Scope\n\nM/L\n\n"
        "## Analysis\n\nOptions\n\n## Solution\n\nPending\n\n"
        "## Details\n\nFindings\n"
    )


class AnalysisGitHub(FakeGitHub):
    def __init__(self, ticket: Ticket) -> None:
        super().__init__(ticket)
        self.uploads: list[str] = []
        self.contents: dict[str, bytes] = {}
        self.edits: list[str] = []
        self.mismatch = False
        self.edit_failure = False
        self.upload_interrupted = False

    async def upload_attachments(self, paths: Sequence[Path]) -> list[UploadedAttachment]:
        results = []
        for path in paths:
            self.uploads.append(path.name)
            url = f"https://github.com/user-attachments/files/{len(self.uploads)}/{path.name}"
            content = path.read_bytes()
            self.contents[url] = content
            if self.upload_interrupted:
                raise asyncio.CancelledError
            results.append(UploadedAttachment(path.name, url, hashlib.sha256(content).hexdigest()))
        return results

    async def download_attachment(self, url: str, dest: Path) -> Path:
        dest.write_bytes(b"wrong bytes" if self.mismatch else self.contents[url])
        return dest

    async def edit_body(self, number: int, body: str) -> None:
        self.edits.append(body)
        if self.edit_failure:
            raise RuntimeError("Body update failed")
        sections: dict[str, str] = {}
        for block in body.split("\n## "):
            name, _, text = block.removeprefix("## ").partition("\n")
            sections[name] = text.strip()
        self.ticket = self.ticket.model_copy(update={"body_sections": sections})


def make_deps(
    tmp_path: Path, *, size: Size = Size.M
) -> tuple[EngineDeps, AnalysisGitHub, ScriptedAdapter]:
    ticket = Ticket(
        number=43,
        title="Add analysis",
        issue_type=IssueType.FEATURE,
        size=size,
        status=Status.TO_DO,
        priority=Priority.MEDIUM,
        body_sections={"Summary": "Need analysis", "Scope": "M/L", "Solution": "Pending"},
    )
    github = AnalysisGitHub(ticket)
    result = AgentResult(
        ok=True,
        final_text="",
        structured_output=handoff().model_dump(),
        session_id="analysis-1",
        usage=None,
        provider="claude",
        authentication_mode=AuthenticationMode.SUBSCRIPTION_LOGIN,
        error=None,
        timed_out=False,
        cancelled=False,
        log_path=None,
        unsupported=None,
    )
    adapter = ScriptedAdapter([ScriptStep(result=result)])
    registry = AdapterRegistry()
    registry.register("claude", lambda config: adapter)
    return (
        EngineDeps(
            config=FugueConfig(),
            store=Store(tmp_path / "store.db"),
            artifacts=ArtifactStore(tmp_path / "artifacts"),
            github=github,
            registry=registry,
            repo_root=tmp_path,
            worktrees_root=tmp_path / "worktrees",
            state_dir=tmp_path,
        ),
        github,
        adapter,
    )


@pytest.mark.parametrize("size", [Size.M, Size.L])
def test_stage_publishes_and_waits_without_selecting(tmp_path: Path, size: Size) -> None:
    deps, github, adapter = make_deps(tmp_path, size=size)
    try:
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.state == RunState.WAITING_FOR_SELECTION
        assert outcome.problems == ()
        assert github.ticket.status == Status.IN_ANALYSIS
        assert github.ticket.body_sections["Solution"] == "Pending"
        assert len(github.uploads) == 2
        assert len(github.edits) == 1
        request = adapter.requests[0]
        assert request.role == Role.ANALYST
        assert request.access == AccessMode.READ_ONLY
        assert request.output_schema == handoff_schema()
        assert not (tmp_path / "worktrees").exists()
        again = asyncio.run(analyse(deps, github.ticket))
        assert again.run_id == outcome.run_id
        assert again.problems == ()
        assert len(adapter.requests) == 1
        assert len(github.uploads) == 2
        assert len(github.edits) == 1
        assert all(op.kind == "identity" for op in deps.store.pending_operations(outcome.run_id))
    finally:
        deps.store.close()


def test_verification_mismatch_blocks_then_reuses_upload(tmp_path: Path) -> None:
    deps, github, adapter = make_deps(tmp_path)
    try:
        github.mismatch = True
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.state == RunState.BLOCKED
        assert "verification mismatch" in outcome.problems[0]
        assert github.edits == []
        github.mismatch = False
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.state == RunState.WAITING_FOR_SELECTION
        assert len(github.uploads) == 2
        assert len(adapter.requests) == 1
    finally:
        deps.store.close()


def test_restart_after_body_failure_reuses_attachments(tmp_path: Path) -> None:
    deps, github, adapter = make_deps(tmp_path)
    try:
        github.edit_failure = True
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.state == RunState.BLOCKED
        deps.store.close()
        deps.store = Store(tmp_path / "store.db")
        github.edit_failure = False
        outcome = asyncio.run(_StoreClosingEngine(Engine(deps)).resume(outcome.run_id))
        assert outcome.state == RunState.WAITING_FOR_SELECTION
        assert len(github.uploads) == 2
        assert len(adapter.requests) == 1
    finally:
        deps.store.close()


def test_unknown_upload_outcome_blocks_without_duplicate(tmp_path: Path) -> None:
    deps, github, _ = make_deps(tmp_path)
    try:
        github.upload_interrupted = True
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(analyse(deps, github.ticket))
        assert deps.store.list_runs()[0].state == RunState.INTERRUPTED
        github.upload_interrupted = False
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.state == RunState.BLOCKED
        assert "outcome unknown" in outcome.problems[0]
        assert len(github.uploads) == 1
        assert not github.edits
    finally:
        deps.store.close()


@pytest.mark.parametrize("invalid", ["schema", "provider"])
def test_bad_analyst_output_does_not_publish(tmp_path: Path, invalid: str) -> None:
    deps, github, _ = make_deps(tmp_path)
    try:
        result = AgentResult(
            ok=True,
            final_text="",
            structured_output={},
            session_id=None,
            usage=None,
            provider="claude",
            authentication_mode=AuthenticationMode.SUBSCRIPTION_LOGIN,
            error=None,
            timed_out=False,
            cancelled=False,
            log_path=None,
            unsupported=None,
        )
        if invalid == "provider":
            result = replace(result, ok=False, provider_limited=True, error="Provider limit")
        adapter = ScriptedAdapter([ScriptStep(result=result)])
        deps.registry.register("claude", lambda config: adapter)
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.state == (
            RunState.PROVIDER_LIMITED if invalid == "provider" else RunState.BLOCKED
        )
        assert not github.uploads
        assert not github.edits
    finally:
        deps.store.close()


@pytest.mark.parametrize("size", [Size.XS, Size.S, Size.XL])
def test_other_sizes_do_not_call_analyst(tmp_path: Path, size: Size) -> None:
    deps, github, adapter = make_deps(tmp_path, size=size)
    try:
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.state == RunState.BLOCKED
        assert not adapter.requests
        assert not github.uploads
        assert github.ticket.status == Status.TO_DO
    finally:
        deps.store.close()


@pytest.mark.parametrize("to_green", [False, True])
def test_cli_run_routes_to_analysis(tmp_path: Path, to_green: bool) -> None:
    deps, github, adapter = make_deps(tmp_path)
    try:
        wrapper = _StoreClosingEngine(Engine(deps))
        outcome = asyncio.run(wrapper.run_to_green(43) if to_green else wrapper.run_to_pr(43))
        assert outcome.state == RunState.WAITING_FOR_SELECTION
        assert len(adapter.requests) == 1
        assert github.ticket.status == Status.IN_ANALYSIS
    finally:
        deps.store.close()


@pytest.mark.parametrize("failure", ["name", "digest", "url", "missing"])
def test_upload_metadata_mismatch_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    deps, github, _ = make_deps(tmp_path)
    original = github.upload_attachments

    async def bad_upload(paths: Sequence[Path]) -> list[UploadedAttachment]:
        uploaded = await original(paths)
        if failure == "missing":
            return []
        attachment = uploaded[0]
        if failure == "name":
            attachment = replace(attachment, name="unexpected.md")
        elif failure == "digest":
            attachment = replace(attachment, sha256="0" * 64)
        else:
            attachment = replace(attachment, href="https://example.com/scope.md")
        return [attachment]

    monkeypatch.setattr(github, "upload_attachments", bad_upload)
    try:
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.state == RunState.BLOCKED
        assert outcome.problems
        assert not github.edits
        # Even an invalid upload response must not cause another upload on restart.
        asyncio.run(analyse(deps, github.ticket))
        assert len(github.uploads) == 1
    finally:
        deps.store.close()


def test_body_mutation_reconciles_after_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps, github, _ = make_deps(tmp_path)
    original = github.edit_body

    async def interrupted_edit(number: int, body: str) -> None:
        await original(number, body)
        raise asyncio.CancelledError

    monkeypatch.setattr(github, "edit_body", interrupted_edit)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(analyse(deps, github.ticket))
        monkeypatch.setattr(github, "edit_body", original)
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.state == RunState.WAITING_FOR_SELECTION
        assert len(github.edits) == 1
        assert len(github.uploads) == 2
    finally:
        deps.store.close()


def test_status_mutation_reconciles_after_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deps, github, adapter = make_deps(tmp_path)
    original = github.set_status

    async def interrupted_status(number: int, status: Status) -> None:
        await original(number, status)
        raise asyncio.CancelledError

    monkeypatch.setattr(github, "set_status", interrupted_status)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(analyse(deps, github.ticket))
        monkeypatch.setattr(github, "set_status", original)
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.state == RunState.WAITING_FOR_SELECTION
        assert github.calls.count("status") == 1
        assert len(adapter.requests) == 1
    finally:
        deps.store.close()


def test_saved_handoff_checksum_mismatch_blocks(tmp_path: Path) -> None:
    deps, github, _ = make_deps(tmp_path)
    try:
        first = asyncio.run(analyse(deps, github.ticket))
        ref = deps.artifacts.open_ref(first.run_id, "analysis-handoff.json")
        assert ref is not None
        ref.path.write_text(handoff().model_dump_json() + "\n", encoding="utf-8")
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.state == RunState.BLOCKED
        assert outcome.problems == ("Saved analysis handoff checksum differs",)
        assert len(github.uploads) == 2
    finally:
        deps.store.close()


def test_conversation_rendering_is_optional_and_marks_automatic() -> None:
    conversation = "# Interview for ticket #43\n"
    links = LINKS | {"43-conversation.md": "https://github.com/user-attachments/conversation"}
    original = render_details(43, handoff(), LINKS)
    assert render_details(43, handoff(), LINKS, automatic=True) == original
    rendered = render_details(43, handoff(), links, automatic=True)
    assert "**Conversation:** [43-conversation.md]" in rendered
    assert "](https://github.com/user-attachments/conversation) (automatic)" in rendered
    assert rendered.index("**Conversation:**") < rendered.index("**Key points**")
    attachments = render_attachments(43, handoff(), conversation)
    assert attachments.pop("43-conversation.md") == conversation
    assert attachments == render_attachments(43, handoff())
