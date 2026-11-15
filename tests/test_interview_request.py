"""Interaction request validation and isolated interview reply schemas."""

from typing import Any

import pytest
from pydantic import ValidationError

from fugue.interview.request import InteractionRequest, InterviewReply, reply_schema


def choice_options() -> list[dict[str, Any]]:
    return [
        {"label": "First", "description": "First approach", "preview": "# Preview\n"},
        {"label": "Second", "description": "Second approach"},
    ]


@pytest.mark.parametrize(
    ("kind", "fields"),
    [
        ("single_choice", {"options": choice_options(), "recommended": 0}),
        ("multiple_choice", {"options": choice_options(), "recommended": 1, "allow_custom": True}),
        ("confirmation", {}),
        ("confirmation", {"options": choice_options(), "recommended": 1}),
        ("free_text", {"allow_custom": True}),
        ("information", {}),
    ],
)
def test_valid_request_kinds(kind: str, fields: dict[str, Any]) -> None:
    data = {"kind": kind, "question": "Choose an approach", **fields}
    request = InteractionRequest.model_validate(data)
    assert request.kind == kind
    assert request.question == data["question"]
    assert request.recommended == fields.get("recommended")
    assert request.allow_custom == fields.get("allow_custom", False)
    if request.options is not None:
        assert request.options[0].preview == "# Preview\n"
        assert request.options[1].preview is None
    else:
        assert request.options is None
    assert InteractionRequest.model_validate_json(request.model_dump_json()) == request


@pytest.mark.parametrize("kind", ["single_choice", "multiple_choice"])
@pytest.mark.parametrize("fields", [{}, {"options": None}, {"options": []}])
def test_choice_requires_nonempty_options(kind: str, fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        InteractionRequest.model_validate({"kind": kind, "question": "Choose", **fields})


@pytest.mark.parametrize("kind", ["free_text", "information"])
@pytest.mark.parametrize("options", [[], choice_options()])
def test_nonchoice_rejects_options(kind: str, options: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        InteractionRequest.model_validate({"kind": kind, "question": "Details", "options": options})


@pytest.mark.parametrize("kind", ["single_choice", "multiple_choice", "confirmation"])
@pytest.mark.parametrize("recommended", [-1, 2, 100])
def test_recommended_index_must_be_in_range(kind: str, recommended: int) -> None:
    with pytest.raises(ValidationError):
        InteractionRequest.model_validate(
            {
                "kind": kind,
                "question": "Choose",
                "options": choice_options(),
                "recommended": recommended,
            }
        )


@pytest.mark.parametrize("kind", ["confirmation", "free_text", "information"])
def test_recommended_requires_options(kind: str) -> None:
    with pytest.raises(ValidationError):
        InteractionRequest.model_validate({"kind": kind, "question": "Details", "recommended": 0})


@pytest.mark.parametrize(
    "change",
    [
        {"unexpected": True},
        {"kind": "unknown"},
        {"question": " "},
        {"options": [{"label": "First"}]},
        {"options": [{"label": " ", "description": "Description"}]},
        {"options": [{"label": "First", "description": " "}]},
        {"options": [{"label": "First", "description": "Description", "unexpected": True}]},
    ],
)
def test_invalid_request_fields(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        InteractionRequest.model_validate(
            {"kind": "single_choice", "question": "Choose", "options": choice_options()} | change
        )


def test_reply_with_and_without_request() -> None:
    reply = InterviewReply(message="Ready for analysis", ready=True)
    assert reply.request is None
    assert reply.ready is True
    reply = InterviewReply.model_validate(
        {
            "message": "Some details are needed",
            "request": {"kind": "free_text", "question": "What should change?"},
        }
    )
    assert reply.request is not None
    assert reply.request.kind == "free_text"
    assert reply.ready is False
    assert InterviewReply.model_validate_json(reply.model_dump_json()) == reply


@pytest.mark.parametrize(
    "change",
    [{"unexpected": True}, {"message": " "}, {"request": {"kind": "single_choice"}}],
)
def test_invalid_reply(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        InterviewReply.model_validate({"message": "More details"} | change)


def test_reply_schema_is_fresh_including_nested_objects() -> None:
    first = reply_schema()
    second = reply_schema()
    assert first == second
    assert first is not second
    assert first["additionalProperties"] is False
    assert first["$defs"]["InteractionRequest"]["additionalProperties"] is False
    assert first["$defs"]["InteractionOption"]["additionalProperties"] is False
    first["$defs"]["InteractionRequest"]["properties"]["kind"]["enum"].append("invalid")
    first["properties"].clear()
    assert reply_schema() == second
