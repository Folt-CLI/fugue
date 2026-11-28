"""Labels: `github.label`."""

from collections.abc import Sequence

from fugue.core.github.area import Area
from fugue.models import Label


class Labels(Area):
    async def list(self) -> Sequence[Label]:
        """Every label of the repository."""
        with self._github.guard(f"labels of {self._slug}"):
            response = await self._client.rest.issues.async_list_labels_for_repo(
                self._owner, self._name, per_page=self.MAX_LIMIT
            )
        return [
            Label(name=label.name, color=label.color, description=label.description or "")
            for label in self._each(response.parsed_data)
        ]

    async def create(self, name: str, color: str = "ededed", description: str = "") -> Label:
        """Create a label. `color` is a hex code without the leading #."""
        with self._github.guard(f"a new label in {self._slug}"):
            response = await self._client.rest.issues.async_create_label(
                self._owner,
                self._name,
                name=name,
                color=color.lstrip("#"),
                description=description,
            )
        label = response.parsed_data
        return Label(name=label.name, color=label.color, description=label.description or "")

    async def delete(self, name: str) -> None:
        """Delete a label."""
        with self._github.guard(f"label {name!r} in {self._slug}"):
            await self._client.rest.issues.async_delete_label(self._owner, self._name, name)
