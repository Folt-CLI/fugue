"""Boundary cases for ticket facts and rendered engine instructions."""

from pathlib import Path

import pytest

from fugue.engine.facts import (
    SOLUTION_LINK_RE,
    allowed_files,
    branch_name,
    explanation,
    scope_url,
    selected_solution,
    slug,
    worktree_path,
)
from fugue.engine.ports import GitHubPort
from fugue.engine.prompts import commit_message, implementation_prompt, pr_body, repair_prompt
from fugue.github import GitHubClient
from fugue.models import IssueType, Priority, Size, Status, Ticket


def ticket(solution: str = "") -> Ticket:
    return Ticket(
        number=33,
        title="Add the engine",
        issue_type=IssueType.FEATURE,
        size=Size.S,
        status=Status.TO_DO,
        priority=Priority.HIGH,
        body_sections={"Solution": solution},
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("", None),
        ("_Pending._", None),
        (
            "[33-solution-1.md](https://github.com/user-attachments/files/abc)",
            "https://github.com/user-attachments/files/abc",
        ),
        ("[32-solution-1.md](https://github.com/user-attachments/a)", None),
        ("[33-solution-2.md](https://github.com/user-attachments/a)", None),
        ("[33-solution-1.md](https://example.com/a)", None),
        ("[33-solution-1.md](https://github.com/user-attachments/)", None),
        ("[33-solution-1.md](https://github.com/user-attachments/a", None),
    ],
)
def test_scope_url(body: str, expected: str | None) -> None:
    assert scope_url(ticket(body)) == expected
    assert scope_url(ticket().model_copy(update={"body_sections": {"Details": body}})) is None


def test_solution_regex() -> None:
    assert SOLUTION_LINK_RE.search("[33-solution-1.md](https://github.com/user-attachments/a)")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("", False),
        ("Selected Solution", False),
        ("[Selected Solution](url)", True),
    ],
)
def test_selected_solution(body: str, expected: bool) -> None:
    assert selected_solution(ticket(body)) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("- `wrong.py`", []),
        ("You may touch only these files:\n- `a.py`\n- `b.py`\n\n- `wrong.py`", ["a.py", "b.py"]),
        (
            "- You may touch only these files:\n- `a.py` and `b.py`\nend\n- `wrong`",
            ["a.py", "b.py"],
        ),
        ("You may touch only these files\nNo files\n- `wrong.py`", []),
        ("You may touch only these files\n- no backticks\n* `x/`\n+ `y`", ["x/", "y"]),
    ],
)
def test_allowed_files(text: str, expected: list[str]) -> None:
    assert allowed_files(text) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Hello, WORLD!!", "hello-world"),
        ("  A___b---c  ", "a-b-c"),
        ("Café déjà", "cafe-deja"),
        ("世界", ""),
        ("---", ""),
        ("", ""),
        ("a" * 39 + " b", "a" * 39),
        ("x" * 99, "x" * 40),
    ],
)
def test_slug(title: str, expected: str) -> None:
    assert slug(title) == expected


def test_names_and_explanation(tmp_path: Path) -> None:
    item = ticket()
    assert branch_name(item) == "feature/33-add-the-engine"
    assert worktree_path(tmp_path, item) == tmp_path / "33-add-the-engine"
    assert explanation(item.model_copy(update={"title": "  Add the engine  "})) == item.title
    long = item.model_copy(update={"title": "A" * 100})
    assert len(f"Feature[#33]: {explanation(long)}") == 72


def test_renderers() -> None:
    item = ticket()
    scope = "Some exact scope\n- `a.py`"
    assert implementation_prompt(scope).endswith(scope)
    assert "No git commit, push or gh" in implementation_prompt(scope)
    assert repair_prompt("failure").endswith("failure")
    message = commit_message(item, "Why", ["a.py: add behaviour"])
    assert message.render() == (
        "Feature[#33]: Add the engine\n\n**Summary**\nWhy\n\n**Changes**\n- a.py: add behaviour"
    )
    body = pr_body(item, "Why", ["a.py: add behaviour"], "pytest: passed")
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Summary",
        "## Changes",
        "## Related work",
        "## Metadata",
        "## Verification",
        "## Risks and follow-up",
    ]
    assert "Closes #33" in body
    assert "pytest: passed" in body
    configured = item.model_copy(update={"milestone": "Backlog", "assignees": ["developer"]})
    assert "Milestone: Backlog\nAssignee: developer" in pr_body(configured, "Why", [], "")


def test_client_structurally_satisfies_port() -> None:
    client: GitHubPort = GitHubClient("owner/repo", "owner", 2)
    assert client is not None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("add the engine", "Add the engine"),
        ("Add the engine.", "Add the engine"),
        (" \tadd\n the\r\nengine... . \t", "Add the engine"),
        ("add the API", "Add the API"),
        ("", ""),
        (" \n\t", ""),
        ("... . ", ""),
        ("a" * 57 + ". more", "A" + "a" * 56),
        ("a" * 57 + " more", "A" + "a" * 56),
        ("ß" + "a" * 100, "SS" + "a" * 56),
    ],
)
def test_explanation_normalizes_title(title: str, expected: str) -> None:
    item = ticket().model_copy(update={"title": title})
    assert explanation(item) == expected
    assert len(f"Feature[#33]: {explanation(item)}") <= 72
