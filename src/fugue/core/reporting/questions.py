"""A point where a flow stops and asks the person running it."""

from pydantic import BaseModel, ConfigDict


class Question(BaseModel):
    """What a flow asks. `text` is the question; `body` is anything to read before answering,
    in Markdown (a plan, a diff summary); `choices` are the answers, and `default` is the one
    that is preselected."""

    model_config = ConfigDict(frozen=True)

    text: str
    body: str = ""
    choices: tuple[str, ...] = ("yes", "no")
    default: str = "no"
