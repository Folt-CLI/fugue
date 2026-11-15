"""Whole-reply clean review forms and their parsing boundaries."""

import pytest

from fugue.engine.review import CLEAN, parse_findings


@pytest.mark.parametrize(
    "text", ["None", "None.", "N/A", "N/A.", "Nothing", "Nothing.", "No findings", "No findings."]
)
@pytest.mark.parametrize("variant", ["plain", "uppercase", "whitespace"])
def test_whole_reply_without_findings_is_clean(text: str, variant: str) -> None:
    if variant == "uppercase":
        text = text.upper()
    elif variant == "whitespace":
        text = f" \t\n{text}\n\t "
    assert parse_findings(text) is CLEAN


@pytest.mark.parametrize(
    "text",
    [
        "None of these cases",
        "None of the tests cover this",
        "None. There is a crash.",
        "None.\nThere is a crash.",
        "There is a crash.\nNone.",
        "Nothing prevents a crash.",
        "N/A. There is a crash.",
        "No findings. There is a crash.",
        "Unexpected reviewer output",
        "",
        " \n\t ",
    ],
)
def test_other_replies_keep_the_unparsed_output_finding(text: str) -> None:
    result = parse_findings(text)
    assert isinstance(result, list)
    [finding] = result
    assert (finding.index, finding.severity, finding.blocking) == (1, "Medium", True)
    assert finding.summary == (
        "The reviewer output could not be parsed as findings. It said: "
        + (" ".join(text.split()) or "(no output)")
    )
    assert finding.path is None


def test_none_followed_by_a_structured_finding_is_not_clean() -> None:
    result = parse_findings("None.\n## Blocking\n1. High: Crash on empty input. `src/a.py:10`")
    assert isinstance(result, list)
    assert any(
        finding.severity == "High"
        and finding.blocking
        and finding.path == "src/a.py"
        and finding.start_line == 10
        and finding.summary == "Crash on empty input."
        for finding in result
    )
