"""GitHub reads that `fugue new` needs on top of the overview reads; nothing here writes."""

from __future__ import annotations

from dataclasses import dataclass

from fugue.github.overview import OverviewGitHubClient


@dataclass(frozen=True)
class IssueHit:
    number: int
    title: str
    state: str


class NewTicketGitHubClient(OverviewGitHubClient):
    async def current_login(self) -> str:
        output = await self._run("api", "user", "--jq", ".login")
        return output.strip()

    async def milestone_titles(self) -> list[str]:
        items = await self._paginated(f"repos/{self.repo}/milestones?state=all")
        return [str(item["title"]) for item in items]

    async def search_issues(self, query: str, limit: int) -> list[IssueHit]:
        """Open and closed issues matching the words of query; pull requests are dropped."""
        words = " ".join(query.split())
        if not words:
            return []
        output = await self._json(
            "api",
            "-X",
            "GET",
            "search/issues",
            "-f",
            f"q={words} repo:{self.repo} is:issue",
            "-f",
            f"per_page={limit}",
        )
        return [
            IssueHit(int(item["number"]), str(item.get("title") or ""), str(item["state"]))
            for item in output.get("items", [])
            if "pull_request" not in item
        ][:limit]

    async def get_body(self, number: int) -> str:
        issue = await self._json("api", f"repos/{self.repo}/issues/{number}")
        return str(issue.get("body") or "")

    async def issue_state(self, number: int) -> str | None:
        """`open` or `closed`, or None when no such issue exists."""
        try:
            issue = await self._json("api", f"repos/{self.repo}/issues/{number}")
        except Exception as error:
            if "404" in str(getattr(error, "stderr_tail", "")) or "Not Found" in str(
                getattr(error, "stderr_tail", "")
            ):
                return None
            raise
        return None if "pull_request" in issue else str(issue["state"])
