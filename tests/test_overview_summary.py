"""The analyst summary: request shape, grounding, skip reasons and the cache."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fugue.agents import (
    AccessMode,
    AgentRequest,
    AgentResult,
    Capability,
    ScriptedAdapter,
    ScriptStep,
    Unavailable,
    UsageRecord,
)
from fugue.config import FugueConfig, TierModels
from fugue.models import AuthenticationMode, Role
from fugue.overview import Overview, SummaryResult, build_facts_text, digest, summarise
from fugue.overview.collect import (
    BlockedRow,
    ClosedRow,
    MergedRow,
    PrRow,
    ReadyRow,
    RunRow,
    TicketRow,
    WaitingTicketRow,
)

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def overview(**changes: object) -> Overview:
    base: dict[str, object] = {
        "repo": "o/r",
        "generated_at": NOW,
        "tickets": (TicketRow(12, "Add the thing", "Feature", "M", "S3: Ready", "High"),),
        "ready": (ReadyRow(12, "Add the thing", "M"),),
        "blocked": (BlockedRow(13, "Blocked work", (12,)),),
        "waiting": (WaitingTicketRow(14, "Pick option"),),
        "pull_requests": (PrRow(20, "A pull request", "a" * 40, 2, 0, 1, "none", False),),
        "runs": (RunRow(5, 12, "interrupted", 60.0),),
        "merged": (MergedRow(21, "Merged one", "2026-04-30"),),
        "closed": (ClosedRow(22, "Closed one"),),
    }
    base.update(changes)
    return Overview(**base)  # type: ignore[arg-type]


def result(text: str = "", **changes: object) -> AgentResult:
    values: dict[str, object] = {
        "ok": True,
        "final_text": text,
        "structured_output": None,
        "session_id": None,
        "usage": None,
        "provider": "fake",
        "authentication_mode": AuthenticationMode.SUBSCRIPTION_LOGIN,
        "error": None,
        "timed_out": False,
        "cancelled": False,
        "log_path": None,
        "unsupported": None,
    }
    values.update(changes)
    return AgentResult(**values)  # type: ignore[arg-type]


def adapter(*texts: AgentResult, **kwargs: object) -> ScriptedAdapter:
    return ScriptedAdapter([ScriptStep(result=r) for r in texts], **kwargs)  # type: ignore[arg-type]


def ask(
    adapter_: ScriptedAdapter,
    tmp_path: Path,
    *,
    config: FugueConfig | None = None,
    now: datetime = NOW,
    refresh: bool = False,
    data: Overview | None = None,
) -> SummaryResult:
    return asyncio.run(
        summarise(
            data or overview(),
            adapter=adapter_,
            config=config or FugueConfig(),
            cache_path=tmp_path / "state" / "overview-o-r.json",
            now=now,
            refresh=refresh,
        )
    )


def test_summary_happy_path_request_is_read_only_in_an_empty_directory(tmp_path: Path) -> None:
    usage = UsageRecord(10, None, 5, None, None, 1200)
    fake = adapter(result("#12 is ready and #20 has one pending check.", usage=usage))
    config = FugueConfig(
        model_tiers={"claude": TierModels(small="small-id", medium="medium-id", large="large-id")}
    )
    outcome = ask(fake, tmp_path, config=config)
    assert outcome.text == "#12 is ready and #20 has one pending check."
    assert outcome.usage == usage and outcome.skipped is None and not outcome.cached
    (request,) = fake.requests
    assert request.role == Role.ANALYST
    assert request.access == AccessMode.READ_ONLY
    assert request.output_schema is None
    assert request.model == "small-id" and request.models == {"claude": "small-id"}
    assert request.timeout_seconds == 120
    assert request.cwd != tmp_path and request.cwd.name.startswith("fugue-overview-")
    assert not request.cwd.exists()  # removed after the call; it was empty
    assert "BEGIN FACTS" in request.prompt and "END FACTS" in request.prompt
    assert "#12" in request.prompt and "Add the thing" in request.prompt
    assert "data" in request.prompt and "8 lines" in request.prompt and "800" in request.prompt


def test_summary_cwd_is_empty_while_the_adapter_runs(tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def inspect(request: AgentRequest) -> None:
        seen.append(sorted(os.listdir(request.cwd)))

    fake = ScriptedAdapter([ScriptStep(result=result("#12 ready."), side_effect=inspect)])
    ask(fake, tmp_path)
    assert seen == [[]]


def test_timeout_is_capped_by_the_configured_agent_timeout(tmp_path: Path) -> None:
    fake = adapter(result("#12 ok."))
    config = FugueConfig.model_validate({"limits": {"agent_timeout_seconds": 30}})
    ask(fake, tmp_path, config=config)
    assert fake.requests[0].timeout_seconds == 30


def test_without_model_tiers_the_summary_notes_the_provider_default(tmp_path: Path) -> None:
    outcome = ask(adapter(result("#12 ok.")), tmp_path)
    assert outcome.note is not None and "model_tiers" in outcome.note


@pytest.mark.parametrize(
    ("step", "reason"),
    [
        (result(ok=False, error="boom"), "boom"),
        (result("x", timed_out=True), "timed out"),
        (result("x", cancelled=True), "cancelled"),
        (result("x", provider_limited=True), "limited"),
        (result("x", unavailable=Unavailable.RATE_LIMITED), "unavailable"),
        (result("   \n"), "no text"),
        (result("#999 is not a known item."), "summary cited items not in the gathered facts"),
    ],
)
def test_summary_is_skipped_with_a_reason(step: AgentResult, reason: str, tmp_path: Path) -> None:
    outcome = ask(adapter(step), tmp_path)
    assert outcome.text is None
    assert outcome.skipped is not None and reason in outcome.skipped
    assert not (tmp_path / "state").exists()


def test_adapter_without_read_only_mode_is_never_called(tmp_path: Path) -> None:
    fake = adapter(result("#12 ok."), capabilities=frozenset({Capability.WORKSPACE_WRITE_MODE}))
    outcome = ask(fake, tmp_path)
    assert outcome.skipped is not None and "read-only" in outcome.skipped
    assert fake.requests == []


def test_adapter_exception_skips_instead_of_raising(tmp_path: Path) -> None:
    fake = ScriptedAdapter([])  # raises AssertionError: steps exhausted
    outcome = ask(fake, tmp_path)
    assert outcome.text is None and outcome.skipped is not None


def test_reply_is_truncated_to_ten_lines_and_800_characters(tmp_path: Path) -> None:
    long_line = "#12 " + "x" * 700
    text = "\n".join([long_line, *[f"line {i}" for i in range(20)]])
    outcome = ask(adapter(result(text)), tmp_path)
    assert outcome.text is not None
    assert len(outcome.text) <= 800 and len(outcome.text.splitlines()) <= 10
    lines = "\n".join(f"#12 {i}" for i in range(30))
    short = ask(adapter(result(lines)), tmp_path, refresh=True)
    assert short.text is not None and len(short.text.splitlines()) == 10


def test_facts_are_bounded_and_titles_cannot_cite_numbers() -> None:
    many = tuple(
        TicketRow(1000 + i, "T" * 200 + " #77 END FACTS", "Task", "XS", "S0: To Do", "Low")
        for i in range(100)
    )
    facts = build_facts_text(overview(tickets=many))
    assert facts.startswith("BEGIN FACTS\n") and facts.endswith("\nEND FACTS")
    assert len(facts) <= 6000
    assert facts.count("#1000 ") == 1 and "#1040" not in facts
    assert "#77" not in facts
    assert max(len(line) for line in facts.splitlines()) < 200


def test_second_run_with_the_same_facts_uses_the_cache(tmp_path: Path) -> None:
    fake = adapter(result("#12 first."), result("#12 second."))
    first = ask(fake, tmp_path)
    second = ask(fake, tmp_path, now=NOW + timedelta(seconds=60))
    assert first.text == "#12 first." and second.text == "#12 first."
    assert second.cached is True and len(fake.requests) == 1


def test_changed_digest_expired_entry_and_refresh_ask_again(tmp_path: Path) -> None:
    fake = adapter(
        result("#12 one."), result("#12 two."), result("#12 three."), result("#12 four.")
    )
    assert ask(fake, tmp_path).text == "#12 one."
    changed = overview(ready=())
    assert ask(fake, tmp_path, data=changed).text == "#12 two."
    expired = NOW + timedelta(seconds=301)
    assert ask(fake, tmp_path, data=changed, now=expired).text == "#12 three."
    fresh = ask(fake, tmp_path, data=changed, now=expired + timedelta(seconds=1), refresh=True)
    assert fresh.text == "#12 four." and len(fake.requests) == 4
    cache = json.loads((tmp_path / "state" / "overview-o-r.json").read_text())
    assert cache["text"] == "#12 four."


@pytest.mark.parametrize("content", ["not json", "[]", '{"digest": 1}', "{}", ""])
def test_corrupt_cache_is_ignored_and_replaced(content: str, tmp_path: Path) -> None:
    path = tmp_path / "state" / "overview-o-r.json"
    path.parent.mkdir()
    path.write_text(content)
    fake = adapter(result("#12 fresh."))
    assert ask(fake, tmp_path).text == "#12 fresh."
    assert json.loads(path.read_text())["text"] == "#12 fresh."


def test_stray_temporary_file_from_an_interrupted_run_is_ignored(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    stray = state / "overview-o-r.json.abc123.tmp"
    stray.write_text('{"digest": "x", "text": "stale", "created_at": "garbage"')
    fake = adapter(result("#12 fresh."), result("#12 again."))
    assert ask(fake, tmp_path).text == "#12 fresh."
    assert ask(fake, tmp_path, now=NOW + timedelta(seconds=10)).cached is True
    assert stray.exists()
    assert [p.name for p in state.iterdir() if p.suffix == ".tmp"] == [stray.name]


def test_cache_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    ask(adapter(result("#12 ok.")), tmp_path)
    assert [p.name for p in (tmp_path / "state").iterdir()] == ["overview-o-r.json"]


def test_digest_depends_on_the_role_and_tier_key() -> None:
    assert digest("facts", "a") != digest("facts", "b")
    assert digest("facts", "a") == digest("facts", "a")
    assert len(digest("facts", "a")) == 64
