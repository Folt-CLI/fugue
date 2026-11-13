"""Question parsing and Solution marker boundaries."""

import asyncio
from collections.abc import Mapping, Sequence

import pytest

from fugue.process import CommandResult
from fugue.questions.decision import QuestionGitHubClient, mark_selected
from fugue.questions.parse import parse_analysis

# Inline ticket layout from docs/dev/issue-format.md, with attachment links.
BODY = """## Summary
Choose a solution.

## Analysis
> [!IMPORTANT]
> Select an implementation scope.

### Where we are
- The developer selects the solution.

| Comparison | Option 1 | Option 2 |
| --- | --- | --- |
| Diff size | Small | Large |

### Option 1: Keep the process
**You get**
- Existing workflow.

**Changes**
- Document the workflow.

**Watch out**
- Manual selection.

**Codex scope:** [2-solution-1.md](https://example.test/one)

### Option 2: Extend the process
**You get**
- An interactive walkthrough.

**Changes**
- Add a CLI command.

**Watch out**
- More code.

**Codex scope:** [2-solution-2.md](https://example.test/two)

### Recommendation
Option 1: smaller scope.

> [!TIP]
> Reply with the option number.

## Solution
**Codex scope:** [2-solution-1.md](https://example.test/one)
**Codex scope:** [2-solution-2.md](https://example.test/two)

## Details
**Codex scope:** [2-solution-1.md](https://example.test/one)
"""


def test_parse_ticket_layout() -> None:
    analysis = parse_analysis(BODY)
    assert [option.number for option in analysis.options] == [1, 2]
    assert analysis.option(1).name == "Keep the process"
    assert analysis.option(2).you_get == "- An interactive walkthrough."
    assert analysis.option(2).changes == "- Add a CLI command."
    assert analysis.option(2).watch_out == "- More code."
    assert analysis.option(2).scope_url == "https://example.test/two"
    assert analysis.recommendation == "Option 1: smaller scope."
    assert "Where we are" in analysis.overview
    assert "Option 1:" not in analysis.overview


def test_current_process_scope_label_and_crlf() -> None:
    body = BODY.replace("Codex scope", "Implementation scope").replace("\n", "\r\n")
    analysis = parse_analysis(body)
    assert analysis.option(1).scope_name == "2-solution-1.md"
    updated = mark_selected(body, analysis.option(1))
    assert "\r\n" in updated
    assert updated.count("[Selected Solution] [") == 1
    assert "\n" not in updated.replace("\r\n", "")


@pytest.mark.parametrize("body", ["", "## Summary\nNothing", "## Analysis\nPending"])
def test_missing_analysis_or_options(body: str) -> None:
    assert parse_analysis(body).options == ()


def test_incomplete_option_and_unknown_selection() -> None:
    analysis = parse_analysis("## Analysis\n### Option 3: Partial\n**You get**\nA choice.")
    assert analysis.option(3).you_get == "A choice."
    assert analysis.option(3).scope_url == ""
    assert analysis.option(3).changes == ""
    with pytest.raises(ValueError, match="Unknown option"):
        analysis.option(1)
    with pytest.raises(ValueError, match="scope"):
        mark_selected(BODY, analysis.option(3))


def test_duplicate_options_refused() -> None:
    with pytest.raises(ValueError, match="Duplicate option"):
        parse_analysis("## Analysis\n### Option 1: A\n### Option 1: B")


def test_marker_is_idempotent_and_only_changes_solution() -> None:
    analysis = parse_analysis(BODY)
    body = BODY.replace(
        "## Solution\n**Codex scope:** [2-solution-1.md]",
        "## Solution\n**Codex scope:** [[Selected Solution] 2-solution-1.md]",
    )
    updated = mark_selected(body, analysis.option(1))
    assert updated == body
    assert updated.count("[Selected Solution]") == 1
    assert mark_selected(updated, analysis.option(1)) == updated
    switched = mark_selected(updated, analysis.option(2))
    assert switched.count("[Selected Solution]") == 1
    assert "[Selected Solution] 2-solution-2.md" in switched
    assert switched.split("## Solution")[0] == BODY.split("## Solution")[0]
    assert switched.split("## Details")[1] == BODY.split("## Details")[1]


@pytest.mark.parametrize(
    "body",
    [
        BODY.split("## Solution")[0],
        BODY.replace("## Solution", "## Other"),
    ],
)
def test_missing_solution_section_refused(body: str) -> None:
    with pytest.raises(ValueError):
        mark_selected(body, parse_analysis(BODY).option(1))


def test_body_edit_uses_stdin_and_explicit_repository() -> None:
    calls: list[tuple[Sequence[str], str | None]] = []

    async def runner(
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        stdin: str | None = None,
        max_output_bytes: int = 65536,
    ) -> CommandResult:
        calls.append((argv, stdin))
        return CommandResult(tuple(argv), 0, False, False, "", "", False, False, False, None, 0.0)

    client = QuestionGitHubClient("owner/repo", "owner", 1, runner=runner, strip_tokens=False)
    asyncio.run(client.edit_body(2, BODY))
    assert calls == [
        (("gh", "issue", "edit", "2", "--repo", "owner/repo", "--body-file", "-"), BODY)
    ]


def test_fenced_option_examples_are_not_parsed() -> None:
    body = BODY.replace(
        "### Where we are", "```markdown\n### Option 9: Example\n```\n\n### Where we are"
    )
    assert [option.number for option in parse_analysis(body).options] == [1, 2]


def test_fenced_solution_example_is_not_edited() -> None:
    example = "```markdown\n## Solution\n**Codex scope:** [2-solution-1.md](https://example.test/one)\n```\n"
    body = BODY.replace("## Summary", example + "## Summary")
    updated = mark_selected(body, parse_analysis(body).option(1))
    assert updated.startswith(example)
    assert updated.count("[Selected Solution] [") == 1


def test_fenced_duplicate_scope_is_preserved() -> None:
    example = "```\n**Codex scope:** [2-solution-1.md](https://example.test/one)\n```\n"
    body = BODY.replace(
        "## Solution\n**Codex scope:** [2-solution-1.md]",
        "## Solution\n**Codex scope:** [[Selected Solution] 2-solution-1.md]",
    ).replace("## Details", example + "## Details")
    updated = mark_selected(body, parse_analysis(body).option(1))
    assert example in updated
    assert updated.count("[Selected Solution]") == 1


@pytest.mark.parametrize("pending", ["_Pending._", "_Not selected yet._"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_pending_solution_is_filled_and_selection_can_change(pending: str, newline: str) -> None:
    before, rest = BODY.split("## Solution\n")
    _, details = rest.split("## Details\n")
    before = before.replace("## Analysis", "## Scope\nSelect the workflow.\n\n## Analysis")
    body = (before + f"## Solution\n{pending}\n\n## Details\n" + details).replace("\n", newline)
    analysis = parse_analysis(body)
    updated = mark_selected(body, analysis.option(1), reason="Ignored for recommendation")
    solution = (
        "## Solution\n\n"
        "> More details are in the attachment tagged **[Selected Solution]** above: "
        "[Selected Solution] [2-solution-1.md](https://example.test/one)\n\n"
        "- Document the workflow.\n\n"
        "**Affected areas:** see the implementation scope\n\n"
        "**Why this one:** smaller scope.\n\n"
    ).replace("\n", newline)
    assert updated == before.replace("\n", newline) + solution + ("## Details\n" + details).replace(
        "\n", newline
    )
    assert updated.count("[Selected Solution] [") == 1
    assert mark_selected(updated, analysis.option(1)) == updated
    switched = mark_selected(updated, analysis.option(2), reason="Need the walkthrough")
    assert switched.count("[Selected Solution] [") == 1
    assert "[Selected Solution] [2-solution-2.md](https://example.test/two)" in switched
    assert "- Add a CLI command." in switched
    assert "**Why this one:** Need the walkthrough" in switched
    assert switched.split("## Solution")[0] == body.split("## Solution")[0]
    assert switched.split("## Details")[1] == body.split("## Details")[1]


def test_pending_solution_uses_option_affected_areas_and_decision_maker() -> None:
    body = BODY.replace(
        "**Changes**\n- Add a CLI command.",
        "**Changes**\n- Add a CLI command.\n- Add tests.\n\n**Affected areas:** CLI and tests",
    )
    body = body.split("## Solution")[0] + "## Solution\n_Pending._\n"
    updated = mark_selected(body, parse_analysis(body).option(2), by="developer")
    assert "- Add a CLI command.\n- Add tests." in updated
    assert "**Affected areas:** CLI and tests" in updated
    assert "**Why this one:** selected by developer" in updated
