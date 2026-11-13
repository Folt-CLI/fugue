"""Boundary cases for ticket facts and rendered engine instructions."""

from dataclasses import replace
from pathlib import Path

import pytest

from fugue.engine.decision import (
    Block,
    Done,
    Implement,
    OpenPr,
    Prepare,
    Push,
    Snapshot,
    Verify,
    decide,
)
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
from fugue.engine.prompts import (
    MAX_CHANGE_LENGTH,
    MAX_CHANGES,
    commit_message,
    fallback_changes,
    implementation_prompt,
    parse_changes,
    pr_body,
    repair_prompt,
)
from fugue.github import GitHubClient, PullRequest
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
        ("[33-solution-1.md](https://example.com/a)", None),
        ("[33-solution-1.md](https://github.com/user-attachments/)", None),
        ("[33-solution-1.md](https://github.com/user-attachments/a", None),
    ],
)
def test_scope_url(body: str, expected: str | None) -> None:
    assert scope_url(ticket(body)) == expected
    assert scope_url(ticket().model_copy(update={"body_sections": {"Details": body}})) is None


U = "https://github.com/user-attachments/files/"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (f"**Codex scope:** [33-solution-1.md]({U}1)", f"{U}1"),
        (
            f"**Codex scope:** [33-solution-1.md]({U}1)\n"
            f"**Revised Codex scope (x):** [Selected Solution] [33-solution-2.md]({U}2)",
            f"{U}2",
        ),
        (
            f"> Pick the attachment tagged **[Selected Solution]** above: "
            f"[Selected Solution] [33-solution-3.md]({U}3)",
            f"{U}3",
        ),
        (
            f"[33-solution-1.md]({U}1)\n[33-solution-2.md]({U}2)\n[33-solution-3.md]({U}3)",
            f"{U}3",
        ),
        (
            f"[Selected Solution] [33-solution-1.md]({U}1)\n[33-solution-2.md]({U}2)",
            f"{U}1",
        ),
        (f"[Selected Solution]\n[33-solution-1.md]({U}1)\n[33-solution-2.md]({U}2)", f"{U}2"),
        (
            f"[Selected Solution] {'x' * 40} [33-solution-1.md]({U}1)\n[33-solution-2.md]({U}2)",
            f"{U}2",
        ),
        (f"[32-solution-1.md]({U}1)\n[Selected Solution] [32-solution-2.md]({U}2)", None),
        (f"[33-solution-1.md]({U}1)\n[Selected Solution] [32-solution-2.md]({U}2)", f"{U}1"),
    ],
)
def test_scope_url_prefers_selected_then_latest(body: str, expected: str | None) -> None:
    assert scope_url(ticket(body)) == expected


def test_scope_url_ignores_links_outside_solution() -> None:
    item = ticket().model_copy(
        update={
            "body_sections": {
                "Analysis": f"[Selected Solution] [33-solution-2.md]({U}2)",
                "Solution": f"[33-solution-1.md]({U}1)",
            }
        }
    )
    assert scope_url(item) == f"{U}1"


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
    assert implementation_prompt(scope, workdir=Path("/real/worktree"), branch="bug/62").endswith(
        scope
    )
    assert "No git commit, push or gh" in implementation_prompt(
        scope, workdir=Path("/real/worktree"), branch="bug/62"
    )
    assert repair_prompt("failure", workdir=Path("/real/worktree"), branch="bug/62").endswith(
        "failure"
    )
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


SNAPSHOT = Snapshot(
    allowed=("allowed.txt",),
    worktree=True,
    head_sha="head",
    ahead=("Feature[#33]: Add the engine",),
    dirty=(),
    changed=("allowed.txt",),
    remote_head_sha="head",
    pr=None,
    status=Status.IN_IMPLEMENTATION,
    scope_sha256="new",
    implemented_sha256="new",
)


@pytest.mark.parametrize(
    ("changes", "action"),
    [
        (
            {"pr": PullRequest(1, "url"), "dirty": ("other.txt",)},
            Done(PullRequest(1, "url"), "head"),
        ),
        ({"dirty": ("other.txt",)}, Block(("Outside allowed files: other.txt",))),
        ({"changed": ("other.txt",)}, Block(("Outside allowed files: other.txt",))),
        ({"worktree": False, "ahead": ()}, Prepare()),
        ({"status": Status.TO_DO}, Prepare()),
        ({"ahead": (), "remote_head_sha": None}, Implement()),
        ({"implemented_sha256": "old", "dirty": ("allowed.txt",)}, Implement()),
        ({"implemented_sha256": None, "dirty": ("allowed.txt",)}, Verify()),
        ({"remote_head_sha": None}, Push()),
        ({"remote_head_sha": "behind"}, Push()),
        ({}, OpenPr()),
    ],
)
def test_decide_applies_the_first_matching_rule(changes: dict[str, object], action: object) -> None:
    assert decide(replace(SNAPSHOT, **changes)) == action  # type: ignore[arg-type]


def test_implementation_prompt_asks_for_a_changes_section() -> None:
    prompt = implementation_prompt("SCOPE", workdir=Path("/real/worktree"), branch="bug/62")
    assert "SCOPE" in prompt
    assert "## Changes" in prompt


def test_parse_changes_reads_what_why_bullets() -> None:
    text = "Done.\n\n## Changes\n\n- a.py: add parser\n* b.py: cover it\n\n## Notes\n- x"
    assert parse_changes(text) == ["a.py: add parser", "b.py: cover it"]


def test_parse_changes_uses_the_last_section_and_trims() -> None:
    long = "- a.py: " + "x" * 500
    many = "\n".join(f"- f{i}.py: why" for i in range(MAX_CHANGES + 5))
    assert parse_changes("## Changes\n- old.py: old\n\n## Changes\n" + long)[0].endswith("...")
    assert len(parse_changes("## Changes\n" + long)[0]) == MAX_CHANGE_LENGTH
    assert len(parse_changes("## Changes\n" + many)) == MAX_CHANGES


@pytest.mark.parametrize(
    "text",
    [
        "",
        "No section here",
        "## Changes\n",
        "## Changes\n\n## Next\n- a.py: x",
        "## Changes\n- no reason given",
        "## Changes\n- a.py: fine\nplain prose line",
        "## Changes\n- a.py:",
        "## Notes\n- a.py: x",
    ],
)
def test_parse_changes_returns_nothing_when_missing_or_malformed(text: str) -> None:
    assert parse_changes(text) == []


def test_fallback_changes_state_only_the_path() -> None:
    assert fallback_changes(["a.py", "b.py", "a.py"]) == ["a.py: updated", "b.py: updated"]
    assert fallback_changes([]) == []


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("workdir", [Path("/real/worktree"), Path("/real/worktree with spaces")])
def test_prompts_put_prepared_identity_before_scope(workdir: Path, repair: bool) -> None:
    scope = "Your working directory is /placeholder on branch bug/placeholder."
    branch = "bug/62-real-branch"
    builder = repair_prompt if repair else implementation_prompt
    prompt = builder(scope, workdir=workdir, branch=branch)
    preamble = (
        f"Your working directory is {workdir} on branch {branch}. Work only there. "
        "If the scope below names a different worktree path or branch, treat that as a "
        "placeholder and use this directory and branch."
    )
    assert prompt.startswith(preamble + "\n\n")
    assert prompt.endswith(scope)
    assert "No git commit, push or gh" in prompt
