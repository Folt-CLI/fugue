"""Solution marker edits and GitHub decision recording."""

import getpass
import re
from typing import Protocol

from fugue.github import GitHubClient
from fugue.models import Status, Ticket
from fugue.questions.parse import SCOPE_LINK, Option, parse_analysis, prose_only


class QuestionClient(Protocol):
    async def get_ticket(self, number: int) -> Ticket: ...
    async def get_body(self, number: int) -> str: ...
    async def edit_body(self, number: int, text: str) -> None: ...
    async def comment(self, number: int, body: str) -> None: ...
    async def set_status(self, number: int, status: Status) -> None: ...


class QuestionGitHubClient(GitHubClient):
    """Extend the existing client without changing other GitHub operations."""

    async def get_body(self, number: int) -> str:
        issue = await self._json("api", f"repos/{self.repo}/issues/{number}")
        return str(issue.get("body") or "")

    async def edit_body(self, number: int, text: str) -> None:
        await self._run(
            "issue", "edit", str(number), "--repo", self.repo, "--body-file", "-", stdin=text
        )


def mark_selected(
    body: str, option: Option, *, reason: str | None = None, by: str | None = None
) -> str:
    """Fill Solution or mark its existing scope, preserving all other body text."""
    if not option.scope_name or not option.scope_url:
        raise ValueError("Chosen option has no implementation scope")
    headings = list(re.finditer(r"^##[ \t]+([^\r\n]+)\r?$", prose_only(body), re.MULTILINE))
    for index, heading in enumerate(headings):
        if heading[1].strip() != "Solution":
            continue
        start = heading.end()
        if body[start - 1 : start] == "\r":
            start -= 1
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        section = body[start:end]
        links = list(SCOPE_LINK.finditer(prose_only(section)))
        matches = [
            link
            for link in links
            if link["name"].removeprefix("[Selected Solution] ") == option.scope_name
            and link["url"] == option.scope_url
        ]
        generated = section.lstrip().startswith(
            "> More details are in the attachment tagged **[Selected Solution]** above:"
        )
        marked = any(
            link["marker"] or link["name"].startswith("[Selected Solution] ") for link in links
        )
        if generated or not marked or not matches:
            recommendation = parse_analysis(body).recommendation
            recommended = re.fullmatch(
                rf"Option {option.number}:\s*(.+)", recommendation, re.DOTALL
            )
            why = (
                recommended[1]
                if recommended
                else reason or f"selected by {by or getpass.getuser()}"
            )
            changes = "\n".join(
                "- " + re.sub(r"^[-*+]\s+", "", line.strip())
                for line in option.changes.splitlines()
                if line.strip()
            )
            newline = "\r\n" if body[start:].startswith("\r\n") else "\n"
            solution = (
                "> More details are in the attachment tagged **[Selected Solution]** above: "
                f"[Selected Solution] [{option.scope_name}]({option.scope_url})\n\n"
                f"{changes}\n\n"
                f"**Affected areas:** {option.affected_areas or 'see the implementation scope'}\n\n"
                f"**Why this one:** {why}\n\n"
            ).replace("\n", newline)
            return body[:start] + newline * 2 + solution + body[end:]
        if len(matches) != 1:
            raise ValueError("Chosen implementation scope must appear once in Solution")

        active_starts = {link.start() for link in links}

        def replace(
            link: re.Match[str],
            chosen_start: int = matches[0].start(),
            starts: set[int] = active_starts,
        ) -> str:
            if link.start() not in starts:
                return link[0]
            name = link["name"].removeprefix("[Selected Solution] ")
            marker = "[Selected Solution] " if link.start() == chosen_start else ""
            relative_start = link.start("name") - link.start() - 1
            relative_end = link.end("name") - link.start()
            prefix = link[0][:relative_start].removesuffix("[Selected Solution] ")
            return prefix + marker + "[" + name + link[0][relative_end:]

        return body[:start] + SCOPE_LINK.sub(replace, section) + body[end:]
    raise ValueError("Question has no Solution section")


def decision_comment(option: Option) -> str:
    return (
        f"Selected option {option.number}: {option.name}.\n\n"
        f"Implementation scope: [{option.scope_name}]({option.scope_url})"
    )


async def record_decision(
    client: QuestionClient,
    number: int,
    body: str,
    option: Option,
    *,
    reason: str | None = None,
    by: str | None = None,
) -> None:
    updated = mark_selected(body, option, reason=reason, by=by)
    await client.edit_body(number, updated)
    await client.comment(number, decision_comment(option))
    await client.set_status(number, Status.READY_FOR_IMPLEMENTATION)
