"""A repository label."""

from pydantic import BaseModel, ConfigDict


class Label(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    color: str = "ededed"
    description: str = ""
