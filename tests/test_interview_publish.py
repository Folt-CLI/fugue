"""Interview publication reuses the durable analysis attachment journal."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_analysis import handoff, make_deps
from test_interview_session import read, result

from fugue.agents import AccessMode, ScriptedAdapter, ScriptStep
from fugue.analysis.stage import analyse
from fugue.cli.app import _StoreClosingEngine
from fugue.engine import Engine
from fugue.interview.flow import interview_then_analyse
from fugue.interview.record import InterviewRecord
from fugue.interview.render import render_conversation
from fugue.interview.store import InterviewStore
from fugue.models import IssueType, Role, Size, Status
from fugue.workflow import RunState


def test_human_interview_publication_and_restart(tmp_path: Path) -> None:
    deps, github, _ = make_deps(tmp_path, size=Size.L)
    github.ticket.issue_type = IssueType.QUESTION
    adapter = ScriptedAdapter(
        [
            ScriptStep(result=result("What are the boundaries?")),
            ScriptStep(result=result("Three directions and their trade-offs.")),
            ScriptStep(result=result("", structured_output=handoff().model_dump())),
        ]
    )
    deps.registry.register("claude", lambda config: adapter)
    try:
        outcome = asyncio.run(
            interview_then_analyse(
                deps,
                github.ticket,
                reader=read(iter(["  keep intent\nverbatim  ", "/proceed"])),
                print_fn=lambda text: None,
            )
        )
        assert outcome.problems == ()
        assert outcome.state == RunState.WAITING_FOR_SELECTION
        first = adapter.requests[0]
        assert first.access == AccessMode.READ_ONLY
        for text in [
            "Need analysis",
            "M/L",
            "intent",
            "users",
            "boundaries",
            "failure cases",
            "constraints",
            "code touched",
            "one line",
            "one trade-off",
            "never implementation scopes",
        ]:
            assert text in first.prompt
        assert "person:   keep intent\nverbatim  " in adapter.requests[-1].prompt
        assert github.uploads.count("43-conversation.md") == 1
        details = github.ticket.body_sections["Details"]
        assert "**Conversation:** [43-conversation.md]" in details
        assert github.ticket.body_sections["Summary"] == "Need analysis"
        assert github.ticket.body_sections["Scope"] == "M/L"
        assert github.ticket.body_sections["Solution"] == "Pending"
        assert github.ticket.status == Status.IN_ANALYSIS
        store = InterviewStore(tmp_path)
        record = store.finished(github.repo, 43)
        assert record is not None and record.upload is not None
        rendered = render_conversation(record)
        assert rendered.encode() == github.contents[record.upload.href]
        again = asyncio.run(analyse(deps, github.ticket, run_id=outcome.run_id))
        assert again.problems == ()
        assert github.uploads.count("43-conversation.md") == 1
        reloaded = store.finished(github.repo, 43)
        assert reloaded is not None
        assert render_conversation(reloaded) == rendered
    finally:
        deps.store.close()


def test_quit_then_resume_guard_and_human_restart(tmp_path: Path) -> None:
    deps, github, _ = make_deps(tmp_path, size=Size.L)
    github.ticket.issue_type = IssueType.QUESTION
    adapter = ScriptedAdapter([ScriptStep(result=result())])
    deps.registry.register("claude", lambda config: adapter)
    messages: list[object] = []
    try:
        outcome = asyncio.run(
            interview_then_analyse(
                deps,
                github.ticket,
                reader=read(iter(["/quit"])),
                print_fn=messages.append,
            )
        )
        assert outcome.state == RunState.INTERRUPTED
        assert github.uploads == []
        assert github.ticket.status == Status.TO_DO
        assert "next: fugue question 43" in messages
        resumed = asyncio.run(_StoreClosingEngine(Engine(deps)).resume(outcome.run_id))
        assert resumed.problems == ("Interview unfinished; continue with: fugue question 43",)
        assert len(adapter.requests) == 1
    finally:
        deps.store.close()


@pytest.mark.parametrize("finished", [False, True])
def test_analysis_with_existing_record(tmp_path: Path, finished: bool) -> None:
    deps, github, adapter = make_deps(tmp_path)
    record = InterviewRecord(repo=github.repo, ticket=43)
    record.add_turn("person", "Earlier intent")
    if finished:
        record.status = "proceeded"
        record.finished = datetime(2020, 1, 1, tzinfo=UTC)
        record.ended_by = "person"
    InterviewStore(tmp_path).save(record)
    notes: list[str] = []
    deps.progress = notes.append
    try:
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.problems == ()
        assert ("Earlier intent" in adapter.requests[0].prompt) == finished
        assert ("43-conversation.md" in github.uploads) == finished
        assert any("Interview unfinished" in note for note in notes) == (not finished)
    finally:
        deps.store.close()


@pytest.mark.parametrize("needs_human", [False, True])
def test_automatic_roles_prompts_usage_and_publication(tmp_path: Path, needs_human: bool) -> None:
    deps, github, _ = make_deps(tmp_path, size=Size.L)
    github.ticket.issue_type = IssueType.QUESTION
    from fugue.config.schema import TierModels

    deps.config.model_tiers = {
        provider: TierModels(small="small", medium="medium", large="large")
        for provider in ("claude", "codex")
    }
    analyst = ScriptedAdapter(
        [
            ScriptStep(result=result()),
            ScriptStep(result=result("", structured_output=handoff().model_dump())),
        ]
    )
    standin = ScriptedAdapter(
        [
            ScriptStep(
                result=replace(
                    result(),
                    provider="codex",
                    structured_output={
                        "reply": "Only Summary and Scope",
                        "proceed": True,
                        "needs_human": needs_human,
                        "reason": "Need credentials",
                    },
                )
            )
        ]
    )
    deps.registry.register("claude", lambda config: analyst)
    deps.registry.register("codex", lambda config: standin)
    try:
        outcome = asyncio.run(
            interview_then_analyse(
                deps,
                github.ticket,
                automatic=True,
                print_fn=lambda text: None,
            )
        )
        assert outcome.state == (
            RunState.BLOCKED if needs_human else RunState.WAITING_FOR_SELECTION
        )
        assert deps.config.roles[Role.ANALYST].provider == "claude"
        assert deps.config.roles[Role.STAND_IN].provider == "codex"
        request = standin.requests[0]
        assert request.role == Role.STAND_IN
        assert request.access == AccessMode.READ_ONLY
        for text in [
            "Summary and Scope",
            "GLOSSARY.md",
            "Never invent",
            "consent",
            "credentials",
            "billing",
            "merge approval",
            "Intent?",
        ]:
            assert text in request.prompt
        rows = deps.store._connection.execute(
            "SELECT role, provider, tier FROM usage_records WHERE run_id = ?",
            (outcome.run_id,),
        ).fetchall()
        assert ("stand_in", "codex", "medium") in [tuple(row) for row in rows]
        if needs_human:
            assert github.uploads == []
            assert github.ticket.status == Status.TO_DO
        else:
            assert " (automatic)" in github.ticket.body_sections["Details"]
            saved = InterviewStore(tmp_path).finished(github.repo, 43)
            assert saved is not None
            assert "stand-in (automatic): Only Summary and Scope" in render_conversation(saved)
    finally:
        deps.store.close()


def test_recorded_upload_is_reused_by_later_publication_run(tmp_path: Path) -> None:
    deps, github, _ = make_deps(tmp_path)
    record = InterviewRecord(
        repo=github.repo,
        ticket=43,
        status="proceeded",
        ended_by="person",
        finished=datetime(2020, 1, 1, tzinfo=UTC),
    )
    store = InterviewStore(tmp_path)
    store.save(record)
    try:
        first = asyncio.run(analyse(deps, github.ticket))
        assert first.problems == ()
        run = deps.store.create_run(43, github.repo)
        deps.artifacts.write(run.id, "analysis-handoff.json", handoff().model_dump_json())
        second = asyncio.run(analyse(deps, github.ticket, run_id=run.id))
        assert second.problems == ()
        assert github.uploads.count("43-conversation.md") == 1
        assert len(github.uploads) == 5
        saved = store.finished(github.repo, 43)
        assert saved is not None and saved.upload is not None
        assert github.contents[saved.upload.href] == render_conversation(saved).encode()
    finally:
        deps.store.close()
