"""Async GitHub CLI operations with explicit repository and head checks."""

import json
import os
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

from fugue.github.parse import flatten_pages, parse_ticket, project_items
from fugue.models import Component, Disposition, Priority, Size, Status, Ticket
from fugue.process import CommandResult, run_command


class Runner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        stdin: str | None = None,
        max_output_bytes: int = 65536,
    ) -> Awaitable[CommandResult]:
        """Return an awaitable CommandResult."""


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str


@dataclass(frozen=True)
class CheckResult:
    name: str
    bucket: str
    link: str


@dataclass(frozen=True)
class ReviewComment:
    path: str
    line: int
    body: str
    side: str = "RIGHT"


@dataclass
class ProjectFieldIds:
    """Configured or lazily discovered IDs; no repository IDs are built in."""

    project_id: str | None = None
    status_field_id: str | None = None
    size_field_id: str | None = None
    status_options: dict[str, str] = field(default_factory=dict)
    size_options: dict[str, str] = field(default_factory=dict)
    priority_field_id: int | None = None
    component_field_id: int | None = None
    disposition_field_id: int | None = None


class GitHubError(RuntimeError):
    def __init__(self, argv: Sequence[str], stderr: str) -> None:
        self.argv = tuple(argv)
        self.stderr_tail = stderr[-1500:]
        super().__init__(f"{self.argv!r} failed:\n{self.stderr_tail}")


class HeadChanged(RuntimeError):
    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Pull request head changed: {actual} != {expected}")


_ITEM_QUERY = (
    "query($o:String!,$r:String!,$n:Int!,$endCursor:String){"
    "repository(owner:$o,name:$r){issue(number:$n){"
    "projectItems(first:100,after:$endCursor){pageInfo{hasNextPage endCursor} "
    "nodes{id project{number owner{"
    "... on Organization{login} ... on User{login}}} "
    'status:fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name}} '
    'size:fieldValueByName(name:"Size"){... on ProjectV2ItemFieldSingleSelectValue{name}}'
    "}}}}}"
)


class GitHubClient:
    def __init__(
        self,
        repo: str,
        project_owner: str,
        project_number: int,
        *,
        runner: Runner = run_command,
        strip_tokens: bool = True,
        field_ids: ProjectFieldIds | None = None,
        gh_executable: Sequence[str] = ("gh",),
        max_output_bytes: int = 16 * 1024 * 1024,
        project_field_limit: int = 200,
    ) -> None:
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self.max_output_bytes = max_output_bytes
        if project_field_limit <= 0:
            raise ValueError("project_field_limit must be positive")
        self.project_field_limit = project_field_limit
        self.repo = repo.strip()
        self.project_owner = project_owner.strip()
        self.project_number = project_number
        self.runner = runner
        self.strip_tokens = strip_tokens
        self.field_ids = field_ids if field_ids is not None else ProjectFieldIds()
        self.gh_executable = tuple(gh_executable)
        if not self.gh_executable:
            raise ValueError("gh_executable must not be empty")
        self.owner, self.name = self.repo.split("/")

    async def _run(
        self,
        *args: str,
        stdin: str | None = None,
        allowed_codes: Sequence[int] = (0,),
        tail_on_truncation: bool = False,
    ) -> str:
        argv = (*self.gh_executable, *args)
        env = dict(os.environ)
        command = argv
        if self.strip_tokens:
            for token in ("GITHUB_TOKEN", "GH_TOKEN"):
                env.pop(token, None)
            # run_command overlays env onto the parent environment. Remove tokens
            # in the launched process too, without mutating global os.environ.
            command = ("env", "-u", "GITHUB_TOKEN", "-u", "GH_TOKEN", *argv)
        result: CommandResult = await self.runner(
            command, env=env, stdin=stdin, max_output_bytes=self.max_output_bytes
        )
        if result.returncode not in allowed_codes or result.timed_out or result.cancelled:
            raise GitHubError(argv, result.stderr)
        if result.stdout_truncated:
            if tail_on_truncation:
                # run_command keeps the final ceil(cap / 2) bytes. Slice bytes
                # so multibyte text cannot pull the prefix or marker into a tail.
                tail_bytes = self.max_output_bytes - self.max_output_bytes // 2
                return result.stdout.encode("utf-8")[-tail_bytes:].decode("utf-8", errors="replace")
            raise GitHubError(argv, result.stderr + "\noutput truncated")
        return result.stdout

    async def _json(self, *args: str) -> Any:
        return json.loads(await self._run(*args))

    async def _paginated(self, endpoint: str, *args: str) -> list[dict[str, Any]]:
        pages = await self._json("api", endpoint, *args, "--paginate", "--slurp")
        return flatten_pages(pages)

    async def _item(self, number: int) -> dict[str, Any] | None:
        responses = await self._paginated(
            "graphql",
            "-f",
            f"query={_ITEM_QUERY}",
            "-f",
            f"o={self.owner}",
            "-f",
            f"r={self.name}",
            "-F",
            f"n={number}",
        )
        return next(
            (
                item
                for response in responses
                for item in project_items(response)
                if item["project"]["number"] == self.project_number
                and item["project"]["owner"]["login"].casefold() == self.project_owner.casefold()
            ),
            None,
        )

    async def get_ticket(self, number: int) -> Ticket:
        issue = await self._json("api", f"repos/{self.repo}/issues/{number}")
        values = await self._paginated(f"repos/{self.repo}/issues/{number}/issue-field-values")
        item = await self._item(number)
        if item is None:
            raise ValueError(f"Issue {number} is absent from project {self.project_number}")
        blockers = await self._paginated(
            f"repos/{self.repo}/issues/{number}/dependencies/blocked_by"
        )
        return parse_ticket(issue, values, item, blockers)

    async def _project_fields(self) -> None:
        ids = self.field_ids
        if ids.project_id is None:
            project = await self._json(
                "project",
                "view",
                str(self.project_number),
                "--owner",
                self.project_owner,
                "--format",
                "json",
            )
            ids.project_id = project["id"]
        if ids.status_field_id is None or ids.size_field_id is None:
            args = (
                "project",
                "field-list",
                str(self.project_number),
                "--owner",
                self.project_owner,
                "--format",
                "json",
                "--limit",
                str(self.project_field_limit),
            )
            response = await self._json(*args)
            if len(response["fields"]) >= self.project_field_limit:
                raise GitHubError(
                    (*self.gh_executable, *args),
                    f"project field-list reached limit {self.project_field_limit}; "
                    "possible truncation; increase project_field_limit",
                )
            for item in response["fields"]:
                if item["name"] == "Status":
                    ids.status_field_id = item["id"]
                    ids.status_options = {opt["name"]: opt["id"] for opt in item["options"]}
                elif item["name"] == "Size":
                    ids.size_field_id = item["id"]
                    ids.size_options = {opt["name"]: opt["id"] for opt in item["options"]}

    async def _set_single(self, number: int, value: Status | Size) -> None:
        await self._project_fields()
        item = await self._item(number)
        if item is None:
            await self._run(
                "project",
                "item-add",
                str(self.project_number),
                "--owner",
                self.project_owner,
                "--url",
                f"https://github.com/{self.repo}/issues/{number}",
            )
            item = await self._item(number)
        if item is None:
            raise ValueError(f"Could not add issue {number} to project")
        ids = self.field_ids
        fid = ids.status_field_id if isinstance(value, Status) else ids.size_field_id
        options = ids.status_options if isinstance(value, Status) else ids.size_options
        if fid is None or ids.project_id is None:
            raise ValueError("Missing project field IDs")
        await self._run(
            "project",
            "item-edit",
            "--id",
            item["id"],
            "--project-id",
            ids.project_id,
            "--field-id",
            fid,
            "--single-select-option-id",
            options[value.value],
        )

    async def set_status(self, number: int, status: Status) -> None:
        await self._set_single(number, status)

    async def set_size(self, number: int, size: Size) -> None:
        await self._set_single(number, size)

    async def _issue_field(self, number: int, name: str, value: str | list[str]) -> None:
        attribute = {
            "Priority": "priority_field_id",
            "Component": "component_field_id",
            "Disposition": "disposition_field_id",
        }[name]
        fid = getattr(self.field_ids, attribute)
        if fid is None:
            fields = await self._paginated(f"orgs/{self.owner}/issue-fields")
            for item in fields:
                if item["name"] == name:
                    fid = item["id"]
                    setattr(self.field_ids, attribute, fid)
                    break
        if fid is None:
            raise ValueError(f"Missing issue field: {name}")
        await self._run(
            "api",
            "-X",
            "POST",
            f"repos/{self.repo}/issues/{number}/issue-field-values",
            "--input",
            "-",
            stdin=json.dumps({"issue_field_values": [{"field_id": fid, "value": value}]}),
        )

    async def set_priority(self, number: int, priority: Priority) -> None:
        await self._issue_field(number, "Priority", priority.value)

    async def set_components(self, number: int, components: Sequence[Component]) -> None:
        await self._issue_field(number, "Component", [item.value for item in components])

    async def set_disposition(self, number: int, disposition: Disposition) -> None:
        await self._issue_field(number, "Disposition", disposition.value)

    async def comment(self, number: int, body: str) -> None:
        await self._run("issue", "comment", str(number), "--repo", self.repo, "--body", body)

    async def find_pr(self, branch: str) -> PullRequest | None:
        items = await self._json(
            "pr",
            "list",
            "--repo",
            self.repo,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,url",
            "--limit",
            "1",
        )
        return PullRequest(**items[0]) if items else None

    async def create_pr(
        self,
        branch: str,
        base: str,
        title: str,
        body: str,
        assignee: str,
        milestone: str,
        project_title: str,
    ) -> PullRequest:
        url = (
            (
                await self._run(
                    "pr",
                    "create",
                    "--repo",
                    self.repo,
                    "--base",
                    base,
                    "--head",
                    branch,
                    "--title",
                    title,
                    "--body",
                    body,
                    "--assignee",
                    assignee,
                    "--milestone",
                    milestone,
                    "--project",
                    project_title,
                )
            )
            .strip()
            .splitlines()[-1]
        )
        return PullRequest(int(url.rstrip("/").rsplit("/", 1)[1]), url)

    async def pr_head_sha(self, pr: PullRequest) -> str:
        return (
            await self._run(
                "pr",
                "view",
                str(pr.number),
                "--repo",
                self.repo,
                "--json",
                "headRefOid",
                "--jq",
                ".headRefOid",
            )
        ).strip()

    async def checks(self, pr: PullRequest) -> list[CheckResult]:
        # gh returns 1 for failed checks and 8 for pending checks with valid JSON.
        output = await self._run(
            "pr",
            "checks",
            str(pr.number),
            "--repo",
            self.repo,
            "--json",
            "name,bucket,link",
            allowed_codes=(0, 1, 8),
        )
        return [CheckResult(**item) for item in json.loads(output)]

    async def failed_log_tail(self, run_link: str, max_lines: int = 200) -> str:
        if max_lines < 0:
            raise ValueError("max_lines must not be negative")
        link = urlparse(run_link.strip())
        path = link.path.strip("/").split("/")
        if (
            link.scheme not in ("http", "https")
            or not link.netloc
            or len(path) < 5
            or "/".join(path[:2]).casefold() != self.repo.casefold()
            or path[2:4] != ["actions", "runs"]
            or not path[4].isascii()
            or not path[4].isdecimal()
        ):
            raise ValueError("Expected an Actions run link for the configured repository")
        run_id = path[4]
        output = await self._run(
            "run",
            "view",
            run_id,
            "--repo",
            self.repo,
            "--log-failed",
            tail_on_truncation=True,
        )
        return "\n".join(output.splitlines()[-max_lines:]) if max_lines else ""

    async def post_review(
        self,
        pr: PullRequest,
        head_sha: str,
        body: str,
        comments: Sequence[ReviewComment] = (),
    ) -> None:
        await self._run(
            "api",
            f"repos/{self.repo}/pulls/{pr.number}/reviews",
            "--input",
            "-",
            stdin=json.dumps(
                {
                    "commit_id": head_sha,
                    "event": "COMMENT",
                    "body": body,
                    "comments": [asdict(comment) for comment in comments],
                }
            ),
        )

    async def merge(self, pr: PullRequest, expected_head_sha: str) -> None:
        actual = await self.pr_head_sha(pr)
        if actual != expected_head_sha:
            raise HeadChanged(expected_head_sha, actual)
        await self._run(
            "pr",
            "merge",
            str(pr.number),
            "--repo",
            self.repo,
            "--squash",
            "--match-head-commit",
            expected_head_sha,
        )
