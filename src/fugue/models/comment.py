"""A comment on an issue or a pull request."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Comment(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    author: str
    body: str
    created_at: datetime
    url: str
