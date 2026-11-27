"""A point where a flow stops and asks the person running it."""

from pydantic import BaseModel, ConfigDict


class Question(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    choices: tuple[str, ...] = ("yes", "no")
    default: str = "no"
