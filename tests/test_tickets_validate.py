"""Validation of a draft against the repository policy, and the rendered sections."""

from pathlib import Path
from typing import Any

import pytest
from test_tickets_create import draft_s

from fugue.config import RepoPolicy
from fugue.engine.facts import allowed_files
from fugue.github.parse import parse_body_sections
from fugue.models import Component, IssueType, Size
from fugue.tickets import TicketDraft, problems, render_body, render_scope
from fugue.tickets.render import SCOPE_SENTENCE

POLICY = RepoPolicy()


def check(draft: TicketDraft, **options: Any) -> list[str]:
    options.setdefault("milestones", ["Backlog", "0.1.0"])
    options.setdefault("open_numbers", {1, 7})
    return problems(draft, POLICY, options.pop("known", {1, 7, 9}), **options)


def test_a_complete_draft_has_no_problems() -> None:
    assert check(draft_s()) == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"summary": ""}, "Summary must be non-empty"),
        ({"scope_text": "  "}, "Scope must be non-empty"),
        ({"title": ""}, "Title must be non-empty"),
        ({"components": []}, "requires exactly one Component"),
        (
            {"components": [Component.SOURCE_CODE, Component.TESTS]},
            "Size S allows at most 1 Components",
        ),
        ({"decision": ""}, "Solution must be non-empty"),
        ({"milestone": "9.9.9"}, "Milestone does not exist: 9.9.9"),
        ({"parent": 42}, "Parent #42 does not exist"),
        ({"blocked_by": [43]}, "Blocker #43 does not exist"),
        ({"blocked_by": [9]}, "Blocker #9 is not open"),
    ],
)
def test_validation_lists_the_reason(changes: dict[str, Any], message: str) -> None:
    assert any(message in item for item in check(draft_s(**changes)))


def test_the_listed_problems_all_appear_together() -> None:
    found = check(draft_s(summary="", scope_text="", title="", components=[]))
    assert len(found) >= 4


def test_s_requires_allowed_files() -> None:
    found = check(draft_s(allowed_files=[], new_files=[]))
    assert "Size S requires allowed files" in found


def test_xs_may_have_no_allowed_files() -> None:
    assert check(draft_s(size=Size.XS, allowed_files=[], new_files=[])) == []


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.py", "src/../../x.py", "a\\b.py"])
def test_paths_must_be_relative_without_dotdot(path: str) -> None:
    found = check(draft_s(allowed_files=[path], new_files=[path]))
    assert any(path in item for item in found)


def test_a_path_must_exist_or_be_marked_new(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "present.py").write_text("")
    present = draft_s(allowed_files=["src/present.py"], new_files=[])
    assert check(present, repo_root=tmp_path) == []
    missing = draft_s(allowed_files=["src/absent.py"], new_files=[])
    found = check(missing, repo_root=tmp_path)
    assert any("does not exist and is not marked new" in item for item in found)
    marked = draft_s(allowed_files=["src/absent.py"], new_files=["src/absent.py"])
    assert check(marked, repo_root=tmp_path) == []


def test_a_path_that_cannot_round_trip_is_refused() -> None:
    found = check(draft_s(allowed_files=["a`b.py"], new_files=["a`b.py"]))
    assert any("a`b.py" in item for item in found)


def test_size_xl_is_refused() -> None:
    found = check(draft_s().model_copy(update={"size": Size.XL}))
    assert any("Size XL" in item for item in found)


def test_m_needs_summary_and_scope_but_no_components() -> None:
    draft = draft_s(size=Size.M, components=[], allowed_files=[], new_files=[], decision="")
    assert check(draft) == []
    assert check(draft.model_copy(update={"summary": ""})) == ["Summary must be non-empty"]


def test_m_allows_two_components_and_refuses_three() -> None:
    two = draft_s(size=Size.M, components=[Component.SOURCE_CODE, Component.TESTS])
    assert check(two) == []
    three = draft_s(
        size=Size.M, components=[Component.SOURCE_CODE, Component.TESTS, Component.USER_DOCS]
    )
    assert check(three) == ["Size M allows at most 2 Components"]


def test_l_is_valid_without_components() -> None:
    assert check(draft_s(size=Size.L, components=[], issue_type=IssueType.FEATURE)) == []


def test_m_body_has_pending_analysis_and_solution() -> None:
    sections = parse_body_sections(render_body(draft_s(size=Size.M)))
    assert sections["Analysis"] == "_Pending._"
    assert sections["Solution"] == "_Pending._"
    assert sections["Summary"] == "Export tickets as CSV."
    assert sections["Scope"] == "Add one export command."


def test_s_body_has_the_solution_layout_and_marker() -> None:
    body = render_body(draft_s(), "[1-solution-1.md](u)", "[1-conversation.md](c)", "<!-- m -->")
    sections = parse_body_sections(body)
    assert list(sections) == ["Summary", "Scope", "Analysis", "Solution", "Details"]
    solution = sections["Solution"]
    assert solution.startswith("> [!IMPORTANT]\n> Add the command.")
    for heading in ("**You get**", "**Changes**", "**Not changed**", "**Watch out**"):
        assert heading in solution
    assert solution.endswith("**Implementation scope:** [1-solution-1.md](u)")
    assert "**Conversation:** [1-conversation.md](c)" in sections["Details"]
    assert body.rstrip().endswith("<!-- m -->")


def test_scope_lists_exactly_the_allowed_files() -> None:
    draft = draft_s()
    scope = render_scope(draft, 5)
    assert SCOPE_SENTENCE in scope
    assert allowed_files(scope) == draft.allowed_files
    headings = [line for line in scope.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Standing rules",
        "## File-by-file changes",
        "## In-scope scenarios",
        "## Out-of-scope scenarios",
        "## Do not change",
        "## Verification",
        "## Final message",
    ]
