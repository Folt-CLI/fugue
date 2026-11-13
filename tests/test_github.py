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
from fugue.github.client import PostedReview, ResolvePermissionError
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
    "body": "## Summary\nGitHub operations\n\n## Scope\nClient\n\n## Analysis\n\n"
    "## Solution\nSelected\n\n## Details\nReference",
}
BLOCKERS: list[dict[str, Any]] = [
    {
        "id": 1011,
        "number": 11,
        "title": "Add models",
        "state": "open",
        "url": "https://api.github.com/repos/example/fugue/issues/11",
    },
    {
        "id": 1010,
        "number": 10,
        "title": "Add process runner",
        "state": "closed",
        "url": "https://api.github.com/repos/example/fugue/issues/10",
    },
]
VALUES: list[dict[str, Any]] = [
    {"issue_field_name": "Priority", "single_select_option": {"name": "High"}},
    {"issue_field_name": "Component", "value": ["Source Code"]},
]
ITEM = {
    "id": "item-16",
    "project": {"number": 2, "owner": {"login": "example"}},
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
        project_owner: str = "example",
        max_output_bytes: int = 16 * 1024 * 1024,
        project_field_limit: int = 200,
    ) -> tuple[GitHubClient, Callable[[], list[dict[str, Any]]]]:
        fixture.write_text(json.dumps({json.dumps(key): value for key, value in responses}))
        record.write_text("")
        client = GitHubClient(
            REPO,
            project_owner,
            2,
            strip_tokens=strip_tokens,
            max_output_bytes=max_output_bytes,
            project_field_limit=project_field_limit,
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
    ticket = parse_ticket(ISSUE, VALUES, ITEM, BLOCKERS)
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
    assert ticket.blocked_by == [11, 10]
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
    }
    assert parse_ticket(minimal, VALUES[:1], ITEM).components == []
    with pytest.raises(ValidationError):
        parse_ticket(ISSUE, VALUES, {**ITEM, "size": {"name": "unknown"}})
    assert parse_body_sections("preamble\n## Summary\ntext\n### Nested\nmore") == {
        "Summary": "text\n### Nested\nmore"
    }


@pytest.mark.parametrize("blockers", [BLOCKERS, []])
def test_get_ticket(fake: Fake, blockers: list[dict[str, Any]]) -> None:
    client, calls = fake(
        [
            (["api", f"repos/{REPO}/issues/16"], response(ISSUE)),
            (["api", f"repos/{REPO}/issues/16/issue-field-values"], response(VALUES)),
            (["api", f"repos/{REPO}/issues/16/dependencies/blocked_by"], response([blockers])),
            (["api", "graphql"], response([PROJECT_RESPONSE])),
        ]
    )
    ticket = asyncio.run(client.get_ticket(16))
    assert ticket == parse_ticket(ISSUE, VALUES, ITEM, blockers)
    assert len(calls()) == 4
    args = calls()[2]["argv"]
    assert args[-8:-2] == ["-f", "o=example", "-f", "r=fugue", "-F", "n=16"]

    assert calls()[3]["argv"] == [
        "api",
        f"repos/{REPO}/issues/16/dependencies/blocked_by",
        "--paginate",
        "--slurp",
    ]
    assert ticket.blocked_by == [item["number"] for item in blockers]


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
            (["api", "graphql"], response([PROJECT_RESPONSE])),
            (["project", "item-edit"], {}),
        ]
    )

    async def scenario() -> None:
        await client.set_status(16, Status.IN_IMPLEMENTATION)
        await client.set_size(16, Size.S)

    asyncio.run(scenario())
    args = [call["argv"] for call in calls()]
    assert args[1] == [
        "project",
        "field-list",
        "2",
        "--owner",
        "example",
        "--format",
        "json",
        "--limit",
        "200",
    ]
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
        "--limit",
        "1",
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
    assert error.value.argv[-4:-2] == ("--json", "number,url")
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
            (
                ["api", f"repos/{REPO}/pulls/27/reviews"],
                response({"id": 9, "commit_id": "sha", "state": "COMMENTED", "body": "Review"}),
            ),
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
            (["api", "graphql"], response([PROJECT_RESPONSE])),
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
            (["api", "graphql"], response([empty])),
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


@pytest.mark.parametrize("newline", ["\r\n", "\r", "\n"])
def test_body_sections_normalize_newlines_and_heading_whitespace(newline: str) -> None:
    body = newline.join(["## Summary \t", "Text", "", "## \t Scope\t ", "More"])
    assert parse_body_sections(body) == {"Summary": "Text", "Scope": "More"}
    ticket = parse_ticket({**ISSUE, "body": body}, VALUES, ITEM)
    assert ticket.body_sections == {"Summary": "Text", "Scope": "More"}


@pytest.mark.parametrize("project_owner", ["example", " ExAmPlE "])
def test_project_lookup_uses_owner_and_number(fake: Fake, project_owner: str) -> None:
    wrong_item = {
        **ITEM,
        "id": "wrong-item",
        "size": {"name": "L"},
        "project": {"number": 2, "owner": {"login": "another-owner"}},
    }
    project_response = {
        "data": {"repository": {"issue": {"projectItems": {"nodes": [wrong_item, ITEM]}}}}
    }
    client, calls = fake(
        [
            (["api", f"repos/{REPO}/issues/16"], response(ISSUE)),
            (["api", f"repos/{REPO}/issues/16/issue-field-values"], response(VALUES)),
            (["api", f"repos/{REPO}/issues/16/dependencies/blocked_by"], response([BLOCKERS])),
            (["api", "graphql"], response([project_response])),
            (["project", "item-edit"], {}),
        ],
        project_owner=project_owner,
        field_ids=ProjectFieldIds(
            project_id="project-id",
            status_field_id="status-id",
            size_field_id="size-id",
            status_options={Status.DONE.value: "done-id"},
        ),
    )

    async def scenario() -> None:
        assert (await client.get_ticket(16)).size == Size.S
        await client.set_status(16, Status.DONE)

    asyncio.run(scenario())
    assert "project{number owner{" in calls()[2]["argv"][3]
    assert "... on Organization{login}" in calls()[2]["argv"][3]
    assert "... on User{login}" in calls()[2]["argv"][3]
    assert calls()[-1]["argv"][3] == "item-16"


@pytest.mark.parametrize(
    "link",
    [
        "https://github.com/another-owner/fugue/actions/runs/123",
        "https://github.com/example/another-repo/actions/runs/123",
        "https://github.com/example/fugue/runs/123",
    ],
)
def test_log_lookup_rejects_ambiguous_repository_or_path(fake: Fake, link: str) -> None:
    client, calls = fake([(["run", "view"], {"stdout": "wrong log"})])
    with pytest.raises(ValueError, match="Actions run link"):
        asyncio.run(client.failed_log_tail(link))
    assert calls() == []


def test_log_lookup_accepts_repository_case_and_outer_whitespace(fake: Fake) -> None:
    client, calls = fake([(["run", "view"], {"stdout": "log"})])
    link = " https://github.com/ExAmPlE/Fugue/actions/runs/123/job/456 \n"
    assert asyncio.run(client.failed_log_tail(link)) == "log"
    assert calls()[0]["argv"] == ["run", "view", "123", "--repo", REPO, "--log-failed"]


@pytest.mark.parametrize("cap", [16 * 1024 * 1024, 128 * 1024])
def test_large_issue_json_respects_capture_limit(fake: Fake, cap: int) -> None:
    body = "## Scope\n" + "x" * 200_000
    client, calls = fake(
        [
            (["api", f"repos/{REPO}/issues/16"], response({**ISSUE, "body": body})),
            (["api", f"repos/{REPO}/issues/16/issue-field-values"], response(VALUES)),
            (["api", f"repos/{REPO}/issues/16/dependencies/blocked_by"], response([BLOCKERS])),
            (["api", "graphql"], response([PROJECT_RESPONSE])),
        ],
        max_output_bytes=cap,
    )
    if cap < len(body):
        with pytest.raises(GitHubError, match="output truncated") as error:
            asyncio.run(client.get_ticket(16))
        assert error.value.argv[-1] == f"repos/{REPO}/issues/16"
        assert len(calls()) == 1
    else:
        assert asyncio.run(client.get_ticket(16)).body_sections == {"Scope": "x" * 200_000}


@pytest.mark.parametrize("extra_byte", [False, True])
def test_checks_json_capture_boundary(fake: Fake, extra_byte: bool) -> None:
    checks = [{"name": "x" * 200_000, "bucket": "pending", "link": "link"}]
    output = json.dumps(checks)
    client, _ = fake(
        [(["pr", "checks"], {"stdout": output, "exit_code": 8})],
        max_output_bytes=len(output.encode()) - int(extra_byte),
    )
    if extra_byte:
        with pytest.raises(GitHubError, match="output truncated"):
            asyncio.run(client.checks(PR))
    else:
        assert asyncio.run(client.checks(PR)) == [CheckResult(**checks[0])]


@pytest.mark.parametrize("cap", [0, -1])
def test_invalid_output_limit(cap: int) -> None:
    with pytest.raises(ValueError, match="max_output_bytes"):
        GitHubClient(REPO, "example", 2, max_output_bytes=cap)


@pytest.mark.parametrize(
    "opening,closing",
    [
        ("```markdown", "```"),
        ("~~~markdown", "~~~"),
        ("````", "`````"),
        ("  ~~~~ example", "   ~~~~  "),
    ],
)
def test_section_headings_inside_fences_are_preserved(opening: str, closing: str) -> None:
    scope = f"Example:\n{opening}\n## Scope\nexample text\n{closing}\nAfter example."
    body = f"## Summary\nIntro\n## Scope\n{scope}\n## Details\nEnd"
    assert parse_body_sections(body) == {"Summary": "Intro", "Scope": scope, "Details": "End"}


@pytest.mark.parametrize(
    "fence,false_close", [("````", "```"), ("~~~", "```"), ("```", "``` trailing")]
)
def test_invalid_closing_fence_keeps_section_open(fence: str, false_close: str) -> None:
    scope = f"{fence}\n{false_close}\n## Scope\nexample"
    assert parse_body_sections(f"## Scope\n{scope}") == {"Scope": scope}


def test_fenced_preamble_does_not_create_sections() -> None:
    assert parse_body_sections("```\n## Scope\nexample\n```\n## Details\nEnd") == {"Details": "End"}


def test_parser_uses_separate_native_blockers() -> None:
    issue = {**ISSUE, "blocked_by": [{"number": 999}]}
    assert parse_ticket(issue, VALUES, ITEM, BLOCKERS).blocked_by == [11, 10]
    assert parse_ticket(issue, VALUES, ITEM, []).blocked_by == []


@pytest.mark.parametrize("paged", [False, True])
def test_blockers_from_multiple_pages(fake: Fake, paged: bool) -> None:
    client, calls = fake(
        [
            (["api", f"repos/{REPO}/issues/16"], response(ISSUE)),
            (["api", f"repos/{REPO}/issues/16/issue-field-values"], response(VALUES)),
            (["api", "graphql"], response([PROJECT_RESPONSE])),
            (
                ["api", f"repos/{REPO}/issues/16/dependencies/blocked_by", "--paginate", "--slurp"],
                response([[BLOCKERS[0]], [BLOCKERS[1]]] if paged else BLOCKERS),
            ),
        ]
    )
    assert asyncio.run(client.get_ticket(16)).blocked_by == [11, 10]
    assert calls()[-1]["argv"][-2:] == ["--paginate", "--slurp"]


@pytest.mark.parametrize("paged", [False, True])
def test_issue_field_discovery_flattens_pages(fake: Fake, paged: bool) -> None:
    first_page = [{"id": 2, "name": "Component"}]
    second_page = [{"id": 1, "name": "Priority"}]
    payload = [first_page, second_page] if paged else first_page + second_page
    client, calls = fake(
        [
            (["api", "orgs/example/issue-fields", "--paginate", "--slurp"], response(payload)),
            (["api", "-X", "POST"], {}),
        ]
    )
    asyncio.run(client.set_priority(16, Priority.HIGH))
    assert calls()[0]["argv"] == ["api", "orgs/example/issue-fields", "--paginate", "--slurp"]
    assert json.loads(calls()[1]["stdin"]) == {
        "issue_field_values": [{"field_id": 1, "value": "High"}]
    }


@pytest.mark.parametrize("limit", [200, 40])
def test_project_field_discovery_beyond_default_limit(fake: Fake, limit: int) -> None:
    fields: list[dict[str, Any]] = [{"id": f"field-{i}", "name": f"Field {i}"} for i in range(40)]
    fields[34] = {
        "id": "status-id",
        "name": "Status",
        "options": [{"id": "done-id", "name": Status.DONE.value}],
    }
    fields[35] = {
        "id": "size-id",
        "name": "Size",
        "options": [{"id": "small-id", "name": Size.S.value}],
    }
    client, calls = fake(
        [
            (
                [
                    "project",
                    "field-list",
                    "2",
                    "--owner",
                    "example",
                    "--format",
                    "json",
                    "--limit",
                    str(limit),
                ],
                response({"fields": fields}),
            ),
            (["api", "graphql"], response([PROJECT_RESPONSE])),
            (["project", "item-edit"], {}),
        ],
        project_field_limit=limit,
        field_ids=ProjectFieldIds(project_id="project-id"),
    )
    if limit == 40:
        with pytest.raises(GitHubError, match="possible truncation") as error:
            asyncio.run(client.set_status(16, Status.DONE))
        assert error.value.argv[-2:] == ("--limit", "40")
        assert len(calls()) == 1
        assert client.field_ids.status_field_id is None
    else:
        asyncio.run(client.set_status(16, Status.DONE))
        assert client.field_ids.status_field_id == "status-id"
        assert client.field_ids.size_field_id == "size-id"
        assert calls()[-1]["argv"][-4:] == [
            "--field-id",
            "status-id",
            "--single-select-option-id",
            "done-id",
        ]


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_project_field_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="project_field_limit"):
        GitHubClient(REPO, "example", 2, project_field_limit=limit)


@pytest.mark.parametrize("max_lines", [3, 200])
@pytest.mark.parametrize("prefix", ["head\n", "head\n[... truncated 5 bytes ...]\n", "界\n"])
def test_failed_log_returns_retained_tail_when_truncated(
    fake: Fake, max_lines: int, prefix: str
) -> None:
    log = prefix * 40_000 + "".join(f"final-{i}\n" for i in range(10))
    cap = 256
    client, _ = fake([(["run", "view"], {"stdout": log})], max_output_bytes=cap)
    result = asyncio.run(
        client.failed_log_tail("https://github.com/example/fugue/actions/runs/123", max_lines)
    )
    tail = log.encode()[-(cap - cap // 2) :].decode(errors="replace")
    assert result == "\n".join(tail.splitlines()[-max_lines:])
    assert result.endswith("final-9")


def test_paginated_project_items_and_issue_field_values(fake: Fake) -> None:
    first_page = {
        "data": {
            "repository": {
                "issue": {
                    "projectItems": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": True, "endCursor": "next"},
                    }
                }
            }
        }
    }
    client, calls = fake(
        [
            (["api", f"repos/{REPO}/issues/16"], response(ISSUE)),
            (
                ["api", f"repos/{REPO}/issues/16/issue-field-values", "--paginate", "--slurp"],
                response([[VALUES[0]], [VALUES[1]]]),
            ),
            (["api", "graphql"], response([first_page, PROJECT_RESPONSE])),
            (["api", f"repos/{REPO}/issues/16/dependencies/blocked_by"], response([])),
        ]
    )
    ticket = asyncio.run(client.get_ticket(16))
    assert ticket.status == Status.IN_IMPLEMENTATION
    assert ticket.components == [Component.SOURCE_CODE]
    assert calls()[2]["argv"][-2:] == ["--paginate", "--slurp"]
    assert "$endCursor:String" in calls()[2]["argv"][3]
    assert "first:100,after:$endCursor" in calls()[2]["argv"][3]
    assert "pageInfo{hasNextPage endCursor}" in calls()[2]["argv"][3]


def test_post_review_sends_event_ranges_and_returns_the_review(fake: Fake) -> None:
    client, calls = fake(
        [
            (
                ["api", f"repos/{REPO}/pulls/27/reviews"],
                response({"id": 5, "commit_id": "abc", "state": "CHANGES_REQUESTED", "body": "b"}),
            )
        ]
    )
    comment = ReviewComment("a.py", 9, "Fix", start_line=7, start_side="RIGHT")
    posted = asyncio.run(client.post_review(PR, "abc", "b", [comment], "REQUEST_CHANGES"))
    assert posted == PostedReview(5, "abc", "CHANGES_REQUESTED", "b")
    assert json.loads(calls()[0]["stdin"]) == {
        "commit_id": "abc",
        "event": "REQUEST_CHANGES",
        "body": "b",
        "comments": [
            {
                "path": "a.py",
                "line": 9,
                "body": "Fix",
                "side": "RIGHT",
                "start_line": 7,
                "start_side": "RIGHT",
            }
        ],
    }


def test_post_review_rejects_unknown_event_without_calling_gh(fake: Fake) -> None:
    client, calls = fake([])
    with pytest.raises(ValueError, match="review event"):
        asyncio.run(client.post_review(PR, "abc", "b", event="DISMISS"))
    assert calls() == []


def test_reviews_lists_pages(fake: Fake) -> None:
    pages = [
        [{"id": 1, "commit_id": "a", "state": "COMMENTED", "body": "x"}],
        [{"id": 2, "commit_id": "b", "state": "APPROVED", "body": None}],
    ]
    client, _ = fake([(["api", f"repos/{REPO}/pulls/27/reviews"], response(pages))])
    assert asyncio.run(client.reviews(PR)) == [
        PostedReview(1, "a", "COMMENTED", "x"),
        PostedReview(2, "b", "APPROVED", ""),
    ]


def thread_page(*resolved: bool) -> dict[str, Any]:
    nodes = [
        {
            "id": f"T{index}",
            "isResolved": value,
            "comments": {"nodes": [{"databaseId": 100 + index, "body": f"body {index}"}]},
        }
        for index, value in enumerate(resolved)
    ]
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": nodes}}}}}


def test_review_threads_and_unresolved_count(fake: Fake) -> None:
    client, calls = fake(
        [(["api", "graphql"], response([thread_page(True, False), thread_page(False)]))]
    )
    threads = asyncio.run(client.review_threads(PR))
    assert [(t.id, t.resolved, t.comments[0].id) for t in threads] == [
        ("T0", True, 100),
        ("T1", False, 101),
        ("T0", False, 100),
    ]
    assert asyncio.run(client.unresolved_thread_count(PR)) == 2
    assert "reviewThreads(first:100,after:$endCursor)" in calls()[0]["argv"][3]
    assert "n=27" in calls()[0]["argv"]


def test_reply_to_thread_posts_to_the_comment(fake: Fake) -> None:
    client, calls = fake([(["api", "-X", "POST"], {})])
    asyncio.run(client.reply_to_thread(PR, 77, "Fixed in abc: done"))
    assert calls()[0]["argv"][:4] == [
        "api",
        "-X",
        "POST",
        f"repos/{REPO}/pulls/27/comments/77/replies",
    ]
    assert json.loads(calls()[0]["stdin"]) == {"body": "Fixed in abc: done"}


def test_resolve_thread_runs_the_mutation(fake: Fake) -> None:
    client, calls = fake([(["api", "graphql"], {})])
    asyncio.run(client.resolve_thread("T9"))
    argv = calls()[0]["argv"]
    assert "resolveReviewThread" in argv[3]
    assert "id=T9" in argv


def test_resolve_thread_uses_the_configured_token_environment(
    fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "ambient")
    monkeypatch.setenv("RESOLVE_TOKEN", "resolver")
    client, calls = fake([(["api", "graphql"], {"expect_env": {"GH_TOKEN": True}})])
    client.resolve_token_env = "RESOLVE_TOKEN"
    asyncio.run(client.resolve_thread("T9"))
    assert len(calls()) == 1
    # Other calls still run without any token.
    client2, _ = fake([(["api", "graphql"], {"expect_env": {"GH_TOKEN": False}})])
    asyncio.run(client2.resolve_thread("T9"))


def test_resolve_thread_requires_the_configured_variable(
    fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RESOLVE_TOKEN", raising=False)
    client, calls = fake([(["api", "graphql"], {})])
    client.resolve_token_env = "RESOLVE_TOKEN"
    with pytest.raises(ValueError, match="RESOLVE_TOKEN"):
        asyncio.run(client.resolve_thread("T9"))
    assert calls() == []


@pytest.mark.parametrize("stderr", ["Resource not accessible by integration", "HTTP 403"])
def test_resolve_thread_permission_failure_is_distinct(fake: Fake, stderr: str) -> None:
    client, _ = fake([(["api", "graphql"], {"stderr": stderr, "exit_code": 1})])
    with pytest.raises(ResolvePermissionError):
        asyncio.run(client.resolve_thread("T9"))


def test_resolve_thread_other_failure_is_plain(fake: Fake) -> None:
    client, _ = fake([(["api", "graphql"], {"stderr": "Could not resolve node", "exit_code": 1})])
    with pytest.raises(GitHubError) as error:
        asyncio.run(client.resolve_thread("T9"))
    assert not isinstance(error.value, ResolvePermissionError)


def test_rerun_failed_reruns_only_failed_jobs(fake: Fake) -> None:
    client, calls = fake([(["run", "rerun"], {})])
    link = "https://github.com/example/fugue/actions/runs/123/job/456"
    asyncio.run(client.rerun_failed(link))
    assert [call["argv"] for call in calls()] == [
        ["run", "rerun", "123", "--repo", REPO, "--failed"]
    ]


@pytest.mark.parametrize(
    "link",
    [
        "https://github.com/example/fugue",
        "https://github.com/other/repo/actions/runs/123",
        "https://github.com/example/fugue/actions/runs/abc",
        "ftp://github.com/example/fugue/actions/runs/123",
    ],
)
def test_rerun_failed_rejects_foreign_links(fake: Fake, link: str) -> None:
    client, calls = fake([(["run", "rerun"], {})])
    with pytest.raises(ValueError, match="Actions run link"):
        asyncio.run(client.rerun_failed(link))
    assert calls() == []


def test_rerun_failed_reports_gh_failure(fake: Fake) -> None:
    client, _ = fake([(["run", "rerun"], {"stderr": "cannot rerun", "exit_code": 1})])
    with pytest.raises(GitHubError, match="cannot rerun"):
        asyncio.run(client.rerun_failed("https://github.com/example/fugue/actions/runs/123"))
