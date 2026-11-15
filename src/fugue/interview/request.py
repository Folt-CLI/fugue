"""Validated interaction requests and the provider-facing interview reply schema."""

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class InteractionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Text
    description: Text
    preview: str | None = None


class InteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["single_choice", "multiple_choice", "confirmation", "free_text", "information"]
    question: Text
    options: Annotated[list[InteractionOption], Field(min_length=1)] | None = None
    recommended: int | None = Field(default=None, ge=0)
    allow_custom: bool = False

    @model_validator(mode="after")
    def consistent_options(self) -> Self:
        if self.kind in {"single_choice", "multiple_choice"} and self.options is None:
            raise ValueError("Choice kinds require options")
        if self.kind in {"free_text", "information"} and self.options is not None:
            raise ValueError("Free text and information must not have options")
        if self.recommended is not None and (
            self.options is None or self.recommended >= len(self.options)
        ):
            raise ValueError("Recommended index must identify an option")
        return self


class InterviewReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Text
    request: InteractionRequest | None = None
    ready: bool = False


def reply_schema() -> dict[str, Any]:
    """Return a fresh JSON schema for structured interview replies."""
    return InterviewReply.model_json_schema()
