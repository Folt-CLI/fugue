"""Scoping of S tickets, trigger checks, policy selection and the implementer's scope."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import test_engine_to_pr
from pydantic import ValidationError
from test_analysis import handoff
from test_engine_to_pr import Harness, result

from fugue.agents import AgentResult, ScriptedAdapter, ScriptStep
from fugue.analysis.render import render_analysis, render_scope_text
from fugue.analysis.schema import Handoff, Scope, ScopingResult, TriggerName
from fugue.config import FugueConfig
from fugue.engine.facts import allowed_files
from fugue.engine.prompts import implementation_prompt, without_orchestrator_notes
from fugue.models import AnalysisDepth, Selection, Size, SizeRule, Status
from fugue.workflow import RunState, Stage

harness = test_engine_to_pr.harness

SCOPE_JSON: dict[str, Any] = {
    "allowed_files": ["allowed.txt"],
    "file_changes": ["allowed.txt: write the content"],
    "in_scope": ["The file holds the content."],
    "verification": ["Run the tests."],
    "final_message": ["List the changes."],
}


def scoped(output: dict[str, Any]) -> AgentResult:
    return replace(result(), provider="claude", structured_output=output)


def scope_depth(harness: Harness) -> None:
    """Configure the "scope" depth for S; it is no longer the default."""
    harness.deps.config.policy.size_rules[Size.S] = SizeRule(
        analysis=AnalysisDepth.SCOPE, review=False, max_components=1
    )


def s_ticket_without_scope(harness: Harness) -> None:
    scope_depth(harness)
    ticket = harness.github.ticket
    parent = ticket.model_copy(
        update={
            "number": 7,
            "issue_type": "Question",
            "body_sections": {"Analysis": "Options.", "Solution": "Option 1 was selected."},
        }
    )
    own = harness.github.get_ticket

    async def get_ticket(number: int) -> Any:
        return parent if number == 7 else await own(number)

    harness.github.get_ticket = get_ticket  # type: ignore[method-assign]
    harness.github.ticket = ticket.model_copy(
        update={
            "body_sections": {**ticket.body_sections, "Solution": "_Not applicable._"},
            "parent": 7,
        }
    )


def analyst(harness: Harness, output: dict[str, Any]) -> ScriptedAdapter:
    adapter = ScriptedAdapter([ScriptStep(result=scoped(output))], name="claude")
    harness.deps.registry.register("claude", lambda config: adapter)
    return adapter


def test_scoping_result_needs_a_scope_when_it_fits() -> None:
    with pytest.raises(ValidationError):
        ScopingResult.model_validate({"fits": True})
    with pytest.raises(ValidationError):
        ScopingResult.model_validate({"fits": False})
    assert ScopingResult.model_validate({"fits": False, "resize_reason": "Open design"})


def test_scope_text_lists_allowed_files_first_and_skips_empty_sections() -> None:
    text = render_scope_text("33 scope", Scope.model_validate(SCOPE_JSON))
    assert text.index("## Allowed files") < text.index("## Changes")
    assert allowed_files(text) == ["allowed.txt"]
    assert "Do not change" not in text and "orchestrator" not in text


def test_s_ticket_without_a_scope_attachment_is_scoped_then_implemented(
    harness: Harness,
) -> None:
    s_ticket_without_scope(harness)
    scoping = analyst(harness, {"fits": True, "scope": SCOPE_JSON})
    outcome = harness.run()
    assert outcome.state == RunState.WAITING_FOR_CI
    assert "download" not in harness.github.calls
    assert len(scoping.requests) == 1
    assert scoping.requests[0].access.value == "read_only"
    assert "Respect every constraint" in scoping.requests[0].prompt
    assert "Files this work is expected to change" in harness.adapter.requests[0].prompt
    stages = [a.stage for a in harness.deps.store.stage_attempts(outcome.run_id)]
    assert Stage.SCOPE in stages


def test_scoping_is_saved_and_not_asked_again_on_a_rerun(
    harness: Harness,
) -> None:
    s_ticket_without_scope(harness)
    scoping = analyst(harness, {"fits": True, "scope": SCOPE_JSON})
    harness.run()
    harness.run()
    assert len(scoping.requests) == 1


def test_a_ticket_that_does_not_fit_is_re_sized_to_m(harness: Harness) -> None:
    s_ticket_without_scope(harness)
    sizes: list[Size] = []

    async def set_size(number: int, size: Size) -> None:
        sizes.append(size)

    harness.github.set_size = set_size  # type: ignore[attr-defined]
    comments: list[str] = []

    async def comment(number: int, body: str) -> None:
        comments.append(body)

    harness.github.comment = comment  # type: ignore[method-assign]
    analyst(harness, {"fits": False, "resize_reason": "It leaves a design question open."})
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert sizes == [Size.M]
    assert comments == ["Re-sized from S to M by scoping: It leaves a design question open."]
    assert "Re-sized to M" in outcome.problems[0]
    assert harness.adapter.requests == []


def test_a_scope_attachment_still_wins_over_scoping(harness: Harness) -> None:
    scope_depth(harness)
    scoping = analyst(harness, {"fits": True, "scope": SCOPE_JSON})
    assert harness.run().state == RunState.WAITING_FOR_CI
    assert scoping.requests == []


def test_s_without_a_scope_is_no_longer_scoped_by_default(harness: Harness) -> None:
    s_ticket_without_scope(harness)
    harness.deps.config.policy.size_rules[Size.S] = FugueConfig().policy.size_rules[Size.S]
    assert harness.deps.config.policy.size_rules[Size.S].analysis == AnalysisDepth.FULL
    scoping = analyst(harness, {"fits": True, "scope": SCOPE_JSON})
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert "A scope attachment is required" in outcome.problems
    assert scoping.requests == []


@pytest.mark.parametrize("size", [Size.XS, Size.S])
def test_xs_and_s_with_a_scope_attachment_run_without_analysis(
    harness: Harness, size: Size
) -> None:
    harness.github.ticket = harness.github.ticket.model_copy(update={"size": size})
    assert harness.deps.config.policy.size_rules[size].analysis == AnalysisDepth.FULL
    scoping = analyst(harness, {"fits": True, "scope": SCOPE_JSON})
    assert harness.run().state == RunState.WAITING_FOR_CI
    assert scoping.requests == []


def triggers(*fired: TriggerName) -> list[dict[str, Any]]:
    return [
        {"name": name, "fired": name in fired, "evidence": "Seen." if name in fired else "None."}
        for name in TriggerName
    ]


def test_handoff_needs_each_trigger_exactly_once() -> None:
    base = handoff().model_dump(mode="json")
    for bad in (triggers()[:3], [*triggers(), triggers()[0]], triggers()[:3] + triggers()[:1]):
        with pytest.raises(ValidationError):
            Handoff.model_validate(base | {"triggers": bad})
    fired = Handoff.model_validate(base | {"triggers": triggers(TriggerName.ARCHITECTURE)})
    assert [check.name for check in fired.fired] == [TriggerName.ARCHITECTURE]


def test_analysis_asks_for_a_reply_only_when_a_trigger_fired() -> None:
    base = handoff().model_dump(mode="json")
    links = {
        "43-code-findings.md": "https://github.com/user-attachments/files/1/findings.md",
        "43-solution-1.md": "https://github.com/user-attachments/files/2/scope.md",
    }
    clear = render_analysis(43, handoff(), links)
    fired = render_analysis(
        43, Handoff.model_validate(base | {"triggers": triggers(TriggerName.IRREVERSIBLE)}), links
    )
    assert "### Trigger check" in clear and "[!TIP]" not in clear
    assert "| Destructive or hard to reverse | yes | Seen. |" in fired and "[!TIP]" in fired


def test_the_size_rules_decide_who_selects() -> None:
    from fugue.config import RepoPolicy

    rules = RepoPolicy().size_rules
    assert rules[Size.XS].selection == Selection.POLICY
    assert rules[Size.S].selection == Selection.POLICY
    assert rules[Size.M].selection == Selection.POLICY
    assert rules[Size.L].selection == Selection.DEVELOPER
    assert not rules[Size.S].review and rules[Size.M].review and not rules[Size.L].review


def test_the_implementer_gets_the_scope_without_the_orchestrator_notes() -> None:
    scope = (
        "# 1 scope\n\n## Allowed files\n\n- `a.py`\n\n## Final message\n\n- Done.\n\n"
        "## Notes for the orchestrator (not for the implementation agent)\n\n"
        "- Test-change trailer: reason\n"
    )
    kept = without_orchestrator_notes(scope)
    assert "Test-change" not in kept and kept.endswith("- Done.\n")
    from pathlib import Path

    prompt = implementation_prompt(scope, workdir=Path("/w"), branch="b")
    assert "Test-change trailer: reason" not in prompt and "Read GLOSSARY.md first" in prompt
    assert (
        without_orchestrator_notes("## Final message\n\n- Done.") == "## Final message\n\n- Done."
    )


def test_status_of_a_scoped_ticket_is_unchanged_by_scoping(harness: Harness) -> None:
    s_ticket_without_scope(harness)
    analyst(harness, {"fits": True, "scope": SCOPE_JSON})
    asyncio.run(asyncio.sleep(0))
    assert harness.github.ticket.status == Status.TO_DO


def policy_deps(tmp_path: Path, *, fired: tuple[TriggerName, ...] = (), size: Size = Size.M) -> Any:
    from test_analysis import make_deps

    deps, github, _ = make_deps(tmp_path, size=size)
    output = handoff().model_dump(mode="json") | {"triggers": triggers(*fired)}
    adapter = ScriptedAdapter([ScriptStep(result=scoped(output))], name="claude")
    deps.registry.register("claude", lambda config: adapter)
    comments: list[str] = []

    async def get_body(number: int) -> str:
        return "\n\n".join(
            f"## {name}\n\n{text}" for name, text in github.ticket.body_sections.items()
        )

    async def comment(number: int, body: str) -> None:
        comments.append(body)

    async def set_status(number: int, status: Status) -> None:
        github.ticket = github.ticket.model_copy(update={"status": status})

    github.get_body = get_body  # type: ignore[attr-defined]
    github.comment = comment  # type: ignore[method-assign]
    github.set_status = set_status  # type: ignore[method-assign]
    return deps, github, comments


def test_m_with_no_trigger_is_selected_by_policy_and_goes_on(tmp_path: Path) -> None:
    from fugue.analysis.stage import analyse
    from fugue.engine.facts import selected_solution

    deps, github, comments = policy_deps(tmp_path)
    try:
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.problems == ()
        assert outcome.state == RunState.RUNNING
        assert selected_solution(github.ticket)
        assert github.ticket.status == Status.READY_FOR_IMPLEMENTATION
        assert comments == [
            "Selected option 1: Publish analysis by policy: no trigger fired.\n\n"
            "Implementation scope: [43-solution-1.md]"
            "(https://github.com/user-attachments/files/2/43-solution-1.md)"
        ]
    finally:
        deps.store.close()


@pytest.mark.parametrize("size", [Size.XS, Size.S])
def test_xs_and_s_are_analysed_in_full_and_selected_by_policy(tmp_path: Path, size: Size) -> None:
    from fugue.analysis.stage import analyse
    from fugue.cli.app import _StoreClosingEngine
    from fugue.engine import Engine
    from fugue.engine.facts import selected_solution

    deps, github, comments = policy_deps(tmp_path, size=size)
    try:
        engine = _StoreClosingEngine(Engine(deps))
        outcome = asyncio.run(engine._analyse_if_needed(43))
        assert outcome is None  # Selected by policy: a lone run goes on to implement.
        assert selected_solution(github.ticket)
        assert github.ticket.status == Status.READY_FOR_IMPLEMENTATION
        assert comments[0].startswith("Selected option 1: Publish analysis by policy")
        assert asyncio.run(analyse(deps, github.ticket)).state == RunState.BLOCKED
    finally:
        deps.store.close()


def test_a_fired_trigger_parks_the_ticket_for_the_developer(tmp_path: Path) -> None:
    from fugue.analysis.stage import analyse
    from fugue.engine.facts import selected_solution

    deps, github, comments = policy_deps(tmp_path, fired=(TriggerName.IRREVERSIBLE,))
    try:
        outcome = asyncio.run(analyse(deps, github.ticket))
        assert outcome.state == RunState.WAITING_FOR_SELECTION
        assert not selected_solution(github.ticket) and comments == []
        assert github.ticket.status == Status.IN_ANALYSIS
    finally:
        deps.store.close()


def test_an_l_question_is_never_selected_by_policy(tmp_path: Path) -> None:
    from fugue.analysis.stage import analyse

    deps, github, comments = policy_deps(tmp_path, size=Size.L)
    try:
        assert asyncio.run(analyse(deps, github.ticket)).state == RunState.WAITING_FOR_SELECTION
        assert comments == []
    finally:
        deps.store.close()


def write_outside(request: Any) -> None:
    workflows = request.cwd / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / "other.yml").write_text("outside")


def watch_resize(harness: Harness) -> tuple[list[Size], list[str]]:
    sizes: list[Size] = []
    comments: list[str] = []

    async def set_size(number: int, size: Size) -> None:
        sizes.append(size)

    async def comment(number: int, body: str) -> None:
        comments.append(body)

    harness.github.set_size = set_size  # type: ignore[attr-defined]
    harness.github.comment = comment  # type: ignore[method-assign]
    return sizes, comments


@pytest.mark.parametrize(("size", "resized"), [(Size.S, False), (Size.XS, False)])
def test_a_run_blocked_on_a_protected_path_does_not_re_size_a_scoped_ticket(
    harness: Harness, size: Size, resized: bool
) -> None:
    harness.github.ticket = harness.github.ticket.model_copy(update={"size": size})
    sizes, comments = watch_resize(harness)
    adapter = ScriptedAdapter(
        [ScriptStep(result=result(), side_effect=write_outside)], name="codex"
    )
    harness.deps.registry.register("codex", lambda config: adapter)
    outcome = harness.run()
    assert outcome.state == RunState.BLOCKED
    assert any("Protected path changed: .github/workflows/other.yml" in p for p in outcome.problems)
    assert (sizes == [Size.M]) == resized
    assert (len(comments) == 1) == resized
    assert any(p.startswith("Re-sized to M:") for p in outcome.problems) == resized
    if resized:
        assert comments[0].startswith("Re-sized from S to M by its run:")


def test_size_records_show_the_original_size_and_a_re_size(harness: Harness) -> None:
    from fugue.store.sizes import size_records

    s_ticket_without_scope(harness)
    watch_resize(harness)
    analyst(harness, {"fits": False, "resize_reason": "It leaves a design question open."})
    harness.run()
    (record,) = size_records(harness.deps.store)
    assert (record.original_size, record.resized_to) == ("S", "M")
    assert (record.selection, record.repairs, record.review_rounds) == ("none", 0, 0)
    assert record.state == "blocked"
    assert size_records(harness.deps.store, ticket_number=99) == []


def test_size_records_tell_policy_from_developer_selection(tmp_path: Path) -> None:
    from fugue.analysis.stage import analyse
    from fugue.store.sizes import size_records

    deps, github, _ = policy_deps(tmp_path)
    try:
        asyncio.run(analyse(deps, github.ticket))
        (record,) = size_records(deps.store)
        assert record.selection == "policy"
    finally:
        deps.store.close()
    (tmp_path / "second").mkdir()
    deps, github, _ = policy_deps(tmp_path / "second", fired=(TriggerName.ARCHITECTURE,))
    try:
        asyncio.run(analyse(deps, github.ticket))
        (record,) = size_records(deps.store)
        assert record.selection == "developer"
    finally:
        deps.store.close()


def test_report_sizes_prints_one_row_per_run(tmp_path: Path) -> None:
    from fugue.cli.render import render_sizes
    from fugue.store.sizes import SizeRecord

    assert render_sizes([]) == "No runs."
    text = render_sizes([SizeRecord(3, 33, "o/r", "blocked", "S", "M", "none", 2, 0)])
    assert text.splitlines()[0].split() == [
        "run",
        "ticket",
        "size",
        "re-sized",
        "to",
        "selection",
        "repairs",
        "reviews",
        "state",
    ]
    assert text.splitlines()[-1].split() == ["3", "#33", "S", "M", "none", "2", "0", "blocked"]
