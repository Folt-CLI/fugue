"""Draft files: `<state_dir>/drafts/<id>.json`, written atomically with mode 0600."""

import contextlib
import os
import re
import tempfile
from pathlib import Path

from pydantic import ValidationError

from fugue.tickets.draft import TicketDraft

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_Ids = list[str]


class DraftStore:
    def __init__(self, state_dir: Path) -> None:
        self.directory = state_dir / "drafts"

    def path(self, draft_id: str) -> Path:
        if not _ID.match(draft_id):
            raise ValueError(f"Unknown draft id: {draft_id}")
        return self.directory / f"{draft_id}.json"

    def save(self, draft: TicketDraft) -> Path:
        """Replace the draft file atomically; it holds the draft and nothing else."""
        target = self.path(draft.id)
        self.directory.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            dir=self.directory, prefix=f"{draft.id}.", suffix=".tmp"
        )
        try:
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(draft.model_dump_json(indent=2))
            os.replace(temporary, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise
        return target

    def load(self, draft_id: str) -> TicketDraft:
        target = self.path(draft_id)
        try:
            draft = TicketDraft.model_validate_json(target.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ValueError(f"Unknown draft id: {draft_id}") from error
        except (OSError, UnicodeError, ValidationError) as error:
            raise ValueError(f"Draft {draft_id} cannot be read: not a saved draft") from error
        if draft.id != draft_id:
            raise ValueError(f"Draft {draft_id} cannot be read: it names another id")
        return draft

    def list(self) -> _Ids:
        if not self.directory.is_dir():
            return []
        return sorted(path.stem for path in self.directory.glob("*.json"))
