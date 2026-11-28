"""A branch on GitHub."""

from pydantic import BaseModel, ConfigDict


class Branch(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    sha: str
    protected: bool = False
