"""GitHub operations exercised exclusively through a fixture-driven child process."""

import asyncio
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from fugue.github import (
    CheckResult,
    GitHubClient,
    GitHubError,
    HeadChanged,
    ProjectFieldIds,
    PullRequest,
    ReviewComment,
)
from fugue.github.parse import parse_body_sections, parse_ticket
from fugue.models import Component, Disposition, Priority, Size, Status

REPO = "example/fugue"
PR = PullRequest(27, "https://github.com/example/fugue/pull/27")
ISSUE: dict[str, Any] = {
    "number": 16,
    "title": "Add GitHub operations",
    "type": {"name": "Feature"},
    "milestone": {"title": "0.1.0"},
    "assignees": [{"login": "developer"}],
    "parent_issue_url": "https://api.github.com/repos/example/fugue/issues/1",
    "blocked_by": [{"number": 11}],
    "body": "## Summary\nGitHub operations\n\n## Scope\nClient\n\n## Analysis\n\n"
    "## Solution\nSelected\n\n## Details\nReference",
}
VALUES: list[dict[str, Any]] = [
    {"issue_field_name": "Priority", "single_select_option": {"name": "High"}},
    {"issue_field_name": "Component", "value": ["Source Code"]},
]
ITEM = {
    "id": "item-16",
    "project": {"number": 2},
    "status": {"name": "S4: In Implementation"},
    "size": {"name": "S"},
}
PROJECT_RESPONSE = {
    "data": {
        "repository": {
            "issue": {
                "projectItems": {
                    "nodes": [
                        {"id": "other", "project": {"number": 99}},
                        ITEM,
                    ]
                }
            }
        }
    }
}


@pytest.fixture
def fake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., tuple[GitHubClient, Callable[[], list[dict[str, Any]]]]]:
    fixture = tmp_path / "fixture.json"
    record = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_GH_FIXTURE", str(fixture))
    monkeypatch.setenv("FAKE_GH_RECORD", str(record))

    def setup(
        responses: list[tuple[list[str], dict[str, Any]]],
        *,
        strip_tokens: bool = True,
        field_ids: ProjectFieldIds | None = None,
    ) -> tuple[GitHubClient, Callable[[], list[dict[str, Any]]]]:
        fixture.write_text(json.dumps({json.dumps(key): value for key, value in responses}))
        record.write_text("")
        client = GitHubClient(
            REPO,
            "example",
            2,
            strip_tokens=strip_tokens,
            field_ids=field_ids,
            gh_executable=[sys.executable, str(Path(__file__).with_name("fake_gh.py"))],
        )

        def calls() -> list[dict[str, Any]]:
            return [json.loads(line) for line in record.read_text().splitlines()]

        return client, calls

    return setup


Fake = Callable[..., tuple[GitHubClient, Callable[[], list[dict[str, Any]]]]]


def response(value: object, code: int = 0) -> dict[str, Any]:
    return {"stdout": json.dumps(value), "exit_code": code}


def test_parse_ticket() -> None:
    ticket = parse_ticket(ISSUE, VALUES, ITEM)
    assert ticket.number == 16
    assert ticket.title == ISSUE["title"]
    assert ticket.issue_type.value == "Feature"
    assert ticket.size == Size.S
    assert ticket.status == Status.IN_IMPLEMENTATION
    assert ticket.priority == Priority.HIGH
    assert ticket.components == [Component.SOURCE_CODE]
    assert ticket.milestone == "0.1.0"
    assert ticket.assignees == ["developer"]
    assert ticket.parent == 1
    assert ticket.blocked_by == [11]
    assert ticket.body_sections == {
        "Summary": "GitHub operations",
        "Scope": "Client",
        "Analysis": "",
        "Solution": "Selected",
        "Details": "Reference",
    }
    minimal = {
        **ISSUE,
        "milestone": None,
        "body": None,
        "assignees": [],
        "parent_issue_url": None,
        "blocked_by": [],
    }
    assert parse_ticket(minimal, VALUES[:1], ITEM).components == []
    with pytest.raises(ValidationError):
        parse_ticket(ISSUE, VALUES, {**ITEM, "size": {"name": "unknown"}})
    assert parse_body_sections("preamble\n## Summary\ntext\n### Nested\nmore") == {
        "Summary": "text\n### Nested\nmore"
    }


def test_get_ticket(fake: Fake) -> None:
    client, calls = fake(
        [
            (["api", f"repos/{REPO}/issues/16"], response(ISSUE)),
            (["api", f"repos/{REPO}/issues/16/issue-field-values"], response(VALUES)),
            (["api", "graphql"], response(PROJECT_RESPONSE)),
        ]
    )
    assert asyncio.run(client.get_ticket(16)) == parse_ticket(ISSUE, VALUES, ITEM)
    assert len(calls()) == 3
    args = calls()[2]["argv"]
    assert args[-6:] == ["-f", "o=example", "-f", "r=fugue", "-F", "n=16"]


def test_project_fields_and_status_size_argv_cached(fake: Fake) -> None:
    client, calls = fake(
        [
            (["project", "view"], response({"id": "project-id"})),
            (
                ["project", "field-list"],
                response(
                    {
                        "fields": [
                            {
                                "id": "status-id",
                                "name": "Status",
                                "options": [
                                    {"id": "doing-id", "name": Status.IN_IMPLEMENTATION.value}
                                ],
                            },
                            {
                                "id": "size-id",
                                "name": "Size",
                                "options": [{"id": "s-id", "name": "S"}],
                            },
                        ]
                    }
                ),
            ),
            (["api", "graphql"], response(PROJECT_RESPONSE)),
            (["project", "item-edit"], {}),
        ]
    )

    async def scenario() -> None:
        await client.set_status(16, Status.IN_IMPLEMENTATION)
        await client.set_size(16, Size.S)

    asyncio.run(scenario())
    args = [call["argv"] for call in calls()]
    assert args[1] == ["project", "field-list", "2", "--owner", "example", "--format", "json"]
    assert args[3] == [
        "project",
        "item-edit",
        "--id",
        "item-16",
        "--project-id",
        "project-id",
        "--field-id",
        "status-id",
        "--single-select-option-id",
        "doing-id",
    ]
    assert args[5][-4:] == ["--field-id", "size-id", "--single-select-option-id", "s-id"]
    assert len(args) == 6


@pytest.mark.parametrize("strip_tokens", [True, False])
def test_tokens_removed_from_child_environment(
    fake: Fake,
    monkeypatch: pytest.MonkeyPatch,
    strip_tokens: bool,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-one")
    monkeypatch.setenv("GH_TOKEN", "secret-two")
    client, _ = fake([(["pr", "view"], {"echo_env": True})], strip_tokens=strip_tokens)
    result = json.loads(asyncio.run(client.pr_head_sha(PR)))
    assert result == {"GITHUB_TOKEN": not strip_tokens, "GH_TOKEN": not strip_tokens}
    assert os.environ["GH_TOKEN"] == "secret-two"


@pytest.mark.parametrize("present", [False, True])
def test_find_pr(fake: Fake, present: bool) -> None:
    client, calls = fake(
        [(["pr", "list"], response([{"number": PR.number, "url": PR.url}] if present else []))]
    )
    assert asyncio.run(client.find_pr("feature/16")) == (PR if present else None)
    assert calls()[0]["argv"] == [
        "pr",
        "list",
        "--repo",
        REPO,
        "--head",
        "feature/16",
        "--state",
        "open",
        "--json",
        "number,url",
    ]


def test_create_pr(fake: Fake) -> None:
    client, calls = fake([(["pr", "create"], {"stdout": PR.url + "\n"})])
    result = asyncio.run(
        client.create_pr(
            "feature/16", "develop", "Title", "Body\nline", "developer", "0.1.0", "Fugue"
        )
    )
    assert result == PR
    assert calls()[0]["argv"] == [
        "pr",
        "create",
        "--repo",
        REPO,
        "--base",
        "develop",
        "--head",
        "feature/16",
        "--title",
        "Title",
        "--body",
        "Body\nline",
        "--assignee",
        "developer",
        "--milestone",
        "0.1.0",
        "--project",
        "Fugue",
    ]


@pytest.mark.parametrize("code,bucket", [(0, "pass"), (1, "fail"), (8, "pending")])
def test_checks(fake: Fake, code: int, bucket: str) -> None:
    client, calls = fake(
        [
            (
                ["pr", "checks"],
                response(
                    [
                        {
                            "name": "test",
                            "bucket": bucket,
                            "link": "https://github.com/actions/runs/123",
                        }
                    ],
                    code,
                ),
            )
        ]
    )
    assert asyncio.run(client.checks(PR)) == [
        CheckResult("test", bucket, "https://github.com/actions/runs/123")
    ]
    assert calls()[0]["argv"] == [
        "pr",
        "checks",
        "27",
        "--repo",
        REPO,
        "--json",
        "name,bucket,link",
    ]


@pytest.mark.parametrize("head", ["expected", "changed"])
def test_merge_head_guard(fake: Fake, head: str) -> None:
    client, calls = fake(
        [
            (["pr", "view"], {"stdout": head + "\n"}),
            (["pr", "merge"], {}),
        ]
    )
    if head == "changed":
        with pytest.raises(HeadChanged) as error:
            asyncio.run(client.merge(PR, "expected"))
        assert error.value.actual == "changed"
        assert error.value.expected == "expected"
        assert len(calls()) == 1
    else:
        asyncio.run(client.merge(PR, "expected"))
        assert calls()[1]["argv"] == [
            "pr",
            "merge",
            "27",
            "--repo",
            REPO,
            "--squash",
            "--match-head-commit",
            "expected",
        ]


def test_error_stderr_tail(fake: Fake) -> None:
    client, _ = fake([(["pr", "list"], {"exit_code": 7, "stderr": "x" * 2000 + "last error"})])
    with pytest.raises(GitHubError) as error:
        asyncio.run(client.find_pr("branch"))
    assert error.value.argv[-2:] == ("--json", "number,url")
    assert error.value.stderr_tail == ("x" * 2000 + "last error")[-1500:]
    assert str(error.value).endswith("last error")


def test_issue_fields_comments_reviews_and_logs(fake: Fake) -> None:
    client, calls = fake(
        [
            (
                ["api", "orgs/example/issue-fields"],
                response(
                    [
                        {"id": 1, "name": "Priority"},
                        {"id": 2, "name": "Component"},
                        {"id": 3, "name": "Disposition"},
                    ]
                ),
            ),
            (["api", "-X", "POST"], {}),
            (["api", f"repos/{REPO}/pulls/27/reviews"], {}),
            (["issue", "comment"], {}),
            (["run", "view"], {"stdout": "first\nsecond\nthird\n"}),
        ]
    )

    async def scenario() -> None:
        await client.set_priority(16, Priority.HIGH)
        await client.set_priority(16, Priority.LOW)
        await client.set_components(16, [Component.SOURCE_CODE, Component.TESTS])
        await client.set_disposition(16, Disposition.MERGED)
        await client.comment(16, "Body\ncomment")
        await client.post_review(PR, "sha", "Review", [ReviewComment("src/a.py", 3, "Fix")])
        link = "https://github.com/example/fugue/actions/runs/123/job/456"
        assert await client.failed_log_tail(link, 2) == "second\nthird"
        assert await client.failed_log_tail(link, 0) == ""
        with pytest.raises(ValueError):
            await client.failed_log_tail(link, -1)
        with pytest.raises(ValueError):
            await client.failed_log_tail("https://github.com/example/fugue")

    asyncio.run(scenario())
    mutations = [
        call
        for call in calls()
        if call["argv"][:4] == ["api", "-X", "POST", f"repos/{REPO}/issues/16/issue-field-values"]
    ]
    assert [json.loads(call["stdin"]) for call in mutations] == [
        {"issue_field_values": [{"field_id": 1, "value": "High"}]},
        {"issue_field_values": [{"field_id": 1, "value": "Low"}]},
        {"issue_field_values": [{"field_id": 2, "value": ["Source Code", "Tests"]}]},
        {"issue_field_values": [{"field_id": 3, "value": "Merged"}]},
    ]
    assert all(call["argv"][-2:] == ["--input", "-"] for call in mutations)
    review = next(call for call in calls() if "reviews" in " ".join(call["argv"]))
    assert json.loads(review["stdin"]) == {
        "commit_id": "sha",
        "event": "COMMENT",
        "body": "Review",
        "comments": [{"path": "src/a.py", "line": 3, "body": "Fix", "side": "RIGHT"}],
    }


def test_configured_project_ids_skip_discovery(fake: Fake) -> None:
    ids = ProjectFieldIds(
        project_id="configured-project",
        status_field_id="configured-status",
        size_field_id="configured-size",
        status_options={Status.DONE.value: "done-option"},
    )
    client, calls = fake(
        [
            (["api", "graphql"], response(PROJECT_RESPONSE)),
            (["project", "item-edit"], {}),
        ],
        field_ids=ids,
    )
    asyncio.run(client.set_status(16, Status.DONE))
    assert len(calls()) == 2
    assert calls()[1]["argv"] == [
        "project",
        "item-edit",
        "--id",
        "item-16",
        "--project-id",
        "configured-project",
        "--field-id",
        "configured-status",
        "--single-select-option-id",
        "done-option",
    ]


def test_missing_project_item_is_added_and_rechecked(fake: Fake) -> None:
    empty: dict[str, Any] = {"data": {"repository": {"issue": {"projectItems": {"nodes": []}}}}}
    client, calls = fake(
        [
            (["api", "graphql"], response(empty)),
            (["project", "item-add"], {}),
        ],
        field_ids=ProjectFieldIds(
            project_id="configured-project",
            status_field_id="status",
            size_field_id="size",
            size_options={"S": "small"},
        ),
    )
    with pytest.raises(ValueError, match="Could not add issue 16"):
        asyncio.run(client.set_size(16, Size.S))
    assert calls()[1]["argv"] == [
        "project",
        "item-add",
        "2",
        "--owner",
        "example",
        "--url",
        "https://github.com/example/fugue/issues/16",
    ]
    assert len(calls()) == 3
    assert calls()[0]["argv"] == calls()[2]["argv"]
