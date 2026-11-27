"""Projects (v2): `github.project`.

Items are addressed by the number of their issue or pull request, and fields by name.
The project defaults to GITHUB_PROJECT.
"""

from collections.abc import MutableSequence, Sequence
from typing import Any, Literal

from fugue.core.github.area import Area
from fugue.errors import GitHubError
from fugue.models import Project, ProjectField, ProjectItem, ProjectOption


class Projects(Area):
    DEFAULT_COLUMNS = ("Status", "Priority", "Size")

    def __init__(self, github: Any) -> None:
        super().__init__(github)
        self._org: bool | None = None

    # ----- projects -----

    async def list(self) -> Sequence[Project]:
        """The projects of the repository's owner."""
        data, _ = await self._request(
            "GET", "", f"projects of {self._owner}", per_page=self.MAX_LIMIT
        )
        return [self._to_project(project) for project in data]

    async def get(self, project: int | None = None) -> Project:
        """One project."""
        number = self._number(project)
        data, _ = await self._request("GET", f"/{number}", f"project {number} of {self._owner}")
        return self._to_project(data)

    async def fields(self, project: int | None = None) -> Sequence[ProjectField]:
        """The fields of a project, with the options of the select fields."""
        number = self._number(project)
        data, _ = await self._request(
            "GET", f"/{number}/fields", f"fields of project {number}", per_page=self.MAX_LIMIT
        )
        return [self._to_field(field) for field in data]

    # ----- items -----

    async def items(
        self,
        project: int | None = None,
        *,
        query: str | None = None,
        columns: Sequence[str] | None = None,
        limit: int = 100,
    ) -> Sequence[ProjectItem]:
        """Issues and pull requests on a project, with the values of the chosen fields.

        `query` is GitHub's project filter, e.g. `status:"S0: To Do"` or `label:bug`.
        """
        number = self._number(project)
        fields = await self.fields(number)
        wanted = self._choose(fields, columns or self.DEFAULT_COLUMNS)
        found: MutableSequence[ProjectItem] = []
        cursor: str | None = None
        while len(found) < limit:
            page, cursor = await self._items_page(number, wanted, query, cursor)
            found.extend(page)
            if cursor is None:
                break
        return found[:limit]

    async def item(
        self, issue: int, project: int | None = None, *, columns: Sequence[str] | None = None
    ) -> ProjectItem:
        """The item for issue or pull request `issue`."""
        number = self._number(project)
        fields = await self.fields(number)
        wanted = self._choose(fields, columns or self.DEFAULT_COLUMNS)
        cursor: str | None = None
        while True:
            page, cursor = await self._items_page(number, wanted, f"#{issue}", cursor)
            for item in page:
                if item.number == issue:
                    return item
            if cursor is None:
                raise GitHubError(f"#{issue} is not on project {number}")

    async def add(
        self, issue: int, project: int | None = None, *, pull_request: bool = False
    ) -> ProjectItem:
        """Put an issue (or a pull request) on the project."""
        number = self._number(project)
        await self._request(
            "POST",
            f"/{number}/items",
            f"#{issue} on project {number}",
            body={
                "type": "PullRequest" if pull_request else "Issue",
                "owner": self._owner,
                "repo": self._name,
                "number": issue,
            },
        )
        return await self.item(issue, number)

    async def set(
        self, issue: int, field: str, value: str, project: int | None = None
    ) -> ProjectItem:
        """Set a field of an item. A select field takes the name of one of its options."""
        number = self._number(project)
        target = self._field(await self.fields(number), field)
        return await self._write(issue, number, target, self._encode(target, value))

    async def clear(self, issue: int, field: str, project: int | None = None) -> ProjectItem:
        """Empty a field of an item."""
        number = self._number(project)
        target = self._field(await self.fields(number), field)
        return await self._write(issue, number, target, None)

    async def remove(self, issue: int, project: int | None = None) -> None:
        """Take an item off the project. The issue itself is not touched."""
        number = self._number(project)
        item = await self.item(issue, number, columns=[])
        await self._request("DELETE", f"/{number}/items/{item.id}", f"#{issue} on project {number}")

    # ----- internals -----

    async def _is_org(self) -> bool:
        if self._org is None:
            self._org = (await self._github.repo.info()).owner_is_org
        return self._org

    def _number(self, project: int | None) -> int:
        number = project or self._github.config.project
        if number is None:
            raise GitHubError("no project: give --project or set GITHUB_PROJECT")
        return number

    async def _request(
        self,
        method: str,
        path: str,
        subject: str,
        *,
        body: dict[str, Any] | None = None,
        **params: Any,
    ) -> tuple[Any, str]:
        """Call the projects API and return the raw JSON and the Link header.

        The generated githubkit models reject field types they do not know yet (for example
        multi-select), so projects are read as plain JSON.
        """
        kind = "orgs" if await self._is_org() else "users"
        url = f"/{kind}/{self._owner}/projectsV2{path}"
        with self._github.guard(subject):
            response = await self._client.arequest(method, url, params=params or None, json=body)
        data = response.json() if response.content else None
        return data, response.headers.get("link", "")

    async def _items_page(
        self, number: int, fields: Sequence[ProjectField], query: str | None, cursor: str | None
    ) -> tuple[Sequence[ProjectItem], str | None]:
        params: dict[str, Any] = {"per_page": self.MAX_LIMIT}
        if fields:
            params["fields"] = ",".join(str(field.id) for field in fields)
        if query:
            params["q"] = query
        if cursor:
            params["after"] = cursor
        data, link = await self._request(
            "GET", f"/{number}/items", f"items of project {number}", **params
        )
        return [self._to_item(item) for item in data], self._next_cursor(link)

    async def _write(self, issue: int, number: int, field: ProjectField, value: Any) -> ProjectItem:
        item = await self.item(issue, number, columns=[])
        await self._request(
            "PATCH",
            f"/{number}/items/{item.id}",
            f"#{issue} on project {number}",
            body={"fields": [{"id": field.id, "value": value}]},
        )
        return await self.item(issue, number, columns=[field.name])

    def _choose(
        self, fields: Sequence[ProjectField], names: Sequence[str]
    ) -> Sequence[ProjectField]:
        """The fields called `names`, in that order. A name that does not exist is an error."""
        return [self._field(fields, name) for name in names]

    @staticmethod
    def _field(fields: Sequence[ProjectField], name: str) -> ProjectField:
        for field in fields:
            if field.name.casefold() == name.casefold():
                return field
        known = ", ".join(field.name for field in fields)
        raise GitHubError(f"the project has no field {name!r}. Fields: {known}")

    @staticmethod
    def _encode(field: ProjectField, value: str) -> Any:
        """The value as GitHub wants it for this kind of field."""
        if field.type == "single_select":
            option = field.option(value)
            if option is None:
                options = ", ".join(o.name for o in field.options)
                raise GitHubError(f"{field.name} has no option {value!r}. Options: {options}")
            return option.id
        if field.type == "number":
            try:
                return float(value)
            except ValueError:
                raise GitHubError(f"{field.name} takes a number, not {value!r}") from None
        if field.type in ("text", "date", "title"):
            return value
        raise GitHubError(f"{field.name} is a {field.type} field and cannot be set here")

    @staticmethod
    def _next_cursor(link: str) -> str | None:
        """The `after` cursor of the next page, from the Link header."""
        for part in link.split(","):
            if 'rel="next"' in part and "after=" in part:
                return part.split("after=")[1].split("&")[0].split(">")[0]
        return None

    @staticmethod
    def _read(source: Any, key: str, default: Any = None) -> Any:
        """A value from a githubkit model or a plain dict."""
        if isinstance(source, dict):
            return source.get(key, default)
        value = getattr(source, key, default)
        return default if type(value).__name__ == "Unset" else value

    @classmethod
    def _text(cls, value: Any) -> str:
        """Any field value as text for display."""
        if value is None:
            return ""
        if isinstance(value, dict):
            for key in ("name", "title", "text"):
                if key in value:
                    return cls._text(value[key])
            if "raw" in value:
                return str(value["raw"])
            if "percent_completed" in value:
                return f"{value['completed']}/{value['total']}"
            return ""
        if isinstance(value, list):
            return ", ".join(cls._text(item) for item in value)
        return str(value)

    def _to_project(self, project: Any) -> Project:
        return Project(
            number=project["number"],
            title=project["title"],
            description=self._read(project, "short_description") or "",
            public=bool(self._read(project, "public", False)),
            closed=self._read(project, "closed_at") is not None,
            url=f"https://github.com/{'orgs' if self._org else 'users'}/{self._owner}"
            f"/projects/{project['number']}",
        )

    def _to_field(self, field: Any) -> ProjectField:
        options = tuple(
            ProjectOption(
                id=str(self._read(option, "id")),
                name=self._text(self._read(option, "name")),
                description=self._text(self._read(option, "description")),
            )
            for option in (self._read(field, "options") or [])
        )
        return ProjectField(
            id=field["id"],
            name=field["name"],
            type=str(self._read(field, "data_type")),
            options=options,
        )

    def _to_item(self, item: Any) -> ProjectItem:
        content = self._read(item, "content")
        content_type = str(self._read(item, "content_type", ""))
        kinds: dict[str, Literal["issue", "pull_request", "draft"]] = {
            "Issue": "issue",
            "PullRequest": "pull_request",
        }
        kind = kinds.get(content_type, "draft")
        state = self._text(self._read(content, "state")) if content is not None else ""
        if kind == "pull_request" and self._read(content, "merged_at"):
            state = "merged"
        values = {
            self._read(field, "name"): self._text(self._read(field, "value"))
            for field in (self._read(item, "fields") or [])
        }
        return ProjectItem(
            id=item["id"],
            kind=kind,
            number=self._read(content, "number") if content is not None else None,
            title=self._text(self._read(content, "title")) if content is not None else "",
            state=state,
            url=self._text(self._read(content, "html_url")) if content is not None else "",
            fields={name: text for name, text in values.items() if name != "Title"},
        )
