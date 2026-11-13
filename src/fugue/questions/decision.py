"""Solution marker edits and GitHub decision recording."""

import re
from typing import Protocol

from fugue.github import GitHubClient
from fugue.models import Status, Ticket
from fugue.questions.parse import SCOPE_LINK, Option, prose_only


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


def mark_selected(body: str, option: Option) -> str:
    """Mark the matching scope in Solution, preserving all other body text."""
    headings = list(re.finditer(r"^##[ \t]+([^\r\n]+)\r?$", prose_only(body), re.MULTILINE))
    for index, heading in enumerate(headings):
        if heading[1].strip() != "Solution":
            continue
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        section = body[start:end]
        links = list(SCOPE_LINK.finditer(prose_only(section)))
        matches = [
            link
            for link in links
            if link["name"].removeprefix("[Selected Solution] ") == option.scope_name
            and link["url"] == option.scope_url
        ]
        if not option.scope_name or not option.scope_url or len(matches) != 1:
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
            if link.start() == chosen_start:
                name = "[Selected Solution] " + name
            relative_start = link.start("name") - link.start()
            relative_end = link.end("name") - link.start()
            return link[0][:relative_start] + name + link[0][relative_end:]

        return body[:start] + SCOPE_LINK.sub(replace, section) + body[end:]
    raise ValueError("Question has no Solution section")


def decision_comment(option: Option) -> str:
    return (
        f"Selected option {option.number}: {option.name}.\n\n"
        f"Implementation scope: [{option.scope_name}]({option.scope_url})"
    )


async def record_decision(client: QuestionClient, number: int, body: str, option: Option) -> None:
    updated = mark_selected(body, option)
    await client.edit_body(number, updated)
    await client.comment(number, decision_comment(option))
    await client.set_status(number, Status.READY_FOR_IMPLEMENTATION)
