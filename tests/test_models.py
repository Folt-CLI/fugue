from enum import StrEnum

import pytest
from pydantic import ValidationError

from fugue.config import RepoPolicy
from fugue.models import (
    AuthenticationMode,
    Component,
    Disposition,
    IssueType,
    Priority,
    Role,
    Size,
    SizeRule,
    Status,
    Ticket,
)


@pytest.mark.parametrize(
    ("enum_type", "values"),
    [
        (Size, ["XS", "S", "M", "L", "XL"]),
        (
            Status,
            [
                "S0: To Do",
                "S1: In Analysis",
                "S3: Ready For Implementation",
                "S4: In Implementation",
                "S5: Done",
            ],
        ),
        (
            IssueType,
            [
                "Task",
                "Bug",
                "Feature",
                "Question",
                "Epic",
                "Maintenance",
                "Documentation",
                "Incident",
                "Test",
            ],
        ),
        (Priority, ["Urgent", "High", "Medium", "Low"]),
        (Component, ["Build", "Dev Docs", "Dev Tools", "Source Code", "Tests", "User Docs"]),
        (
            Disposition,
            [
                "Abandoned",
                "Answered",
                "Duplicate",
                "Invalid",
                "Merged",
                "Obsolete",
                "Out of Scope",
                "Released",
                "Superseded",
                "Withdrawn",
                "Won't Do",
            ],
        ),
        (Role, ["analyst", "implementer", "reviewer"]),
        (AuthenticationMode, ["subscription_login", "api_key"]),
    ],
)
def test_enum_values(enum_type: type[StrEnum], values: list[str]) -> None:
    assert [member.value for member in enum_type] == values
    assert [str(member) for member in enum_type] == values


def ticket_data() -> dict[str, object]:
    return {
        "number": 11,
        "title": "Add configuration and core data models",
        "issue_type": "Feature",
        "size": "S",
        "status": "S4: In Implementation",
        "priority": "High",
        "milestone": "0.1.0",
        "assignees": ["developer"],
        "parent": 1,
        "blocked_by": [10],
        "components": ["Source Code"],
        "body_sections": {"Summary": "Provide validated models."},
    }


def test_ticket_validates_and_preserves_data() -> None:
    data = ticket_data()
    ticket = Ticket.model_validate(data)
    assert ticket.model_dump(mode="json") == data
    assert ticket.issue_type is IssueType.FEATURE
    assert ticket.size is Size.S
    assert ticket.status is Status.IN_IMPLEMENTATION
    assert ticket.priority is Priority.HIGH
    assert ticket.components == [Component.SOURCE_CODE]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("number", 0),
        ("title", ""),
        ("issue_type", "feature"),
        ("size", "XXL"),
        ("status", "S2: Unknown"),
        ("priority", "Critical"),
        ("parent", -1),
        ("blocked_by", ["invalid"]),
        ("components", ["Unknown"]),
        ("body_sections", {"Summary": 123}),
        ("unknown", True),
    ],
)
def test_invalid_ticket_data(field: str, value: object) -> None:
    data = ticket_data()
    data[field] = value
    with pytest.raises(ValidationError) as error:
        Ticket.model_validate(data)
    assert error.value.errors()[0]["loc"][0] == field


def test_ticket_required_fields() -> None:
    with pytest.raises(ValidationError):
        Ticket.model_validate({})


def test_ticket_collection_defaults_are_independent() -> None:
    data = ticket_data()
    for key in ("assignees", "parent", "blocked_by", "components", "body_sections"):
        del data[key]
    first = Ticket.model_validate(data)
    second = Ticket.model_validate(data)
    first.assignees.append("developer")
    first.blocked_by.append(10)
    first.components.append(Component.SOURCE_CODE)
    first.body_sections["Scope"] = "Models"
    assert second.assignees == []
    assert second.blocked_by == []
    assert second.components == []
    assert second.body_sections == {}
    assert second.parent is None


def test_default_size_rules() -> None:
    rules = RepoPolicy().size_rules
    assert {
        size: (rule.analysis, rule.review, rule.max_components) for size, rule in rules.items()
    } == {
        Size.XS: (False, False, 1),
        Size.S: (False, True, 1),
        Size.M: (True, True, 2),
        Size.L: (True, True, None),
        Size.XL: (False, False, 0),
    }


def test_size_rule_rejects_negative_component_limit() -> None:
    with pytest.raises(ValidationError, match="max_components"):
        SizeRule(analysis=False, review=False, max_components=-1)
