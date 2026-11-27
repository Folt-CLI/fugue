"""Pure Markdown renderers for analysis and implementation scope attachments."""

import re
from collections.abc import Mapping, Sequence

from fugue.analysis.schema import TRIGGER_LABELS, Handoff, Scope

ORCHESTRATOR_NOTES_HEADING = "Notes for the orchestrator (not for the implementation agent)"


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _path(item: str) -> str:
    return item if "`" in item else f"`{item}`"


def _scenario(item: str) -> str:
    return re.sub(r"^\s*\d+[.)]\s+", "", item)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def render_analysis(number: int, handoff: Handoff, links: Mapping[str, str]) -> str:
    parts = [
        "## Analysis",
        f"> [!IMPORTANT]\n> {handoff.decision}",
        "### Where we are",
        _bullets(handoff.where_we_are),
        "**Out of scope**",
        _bullets(handoff.out_of_scope),
        "**Terms**",
        _bullets(handoff.terms),
    ]
    if len(handoff.options) > 1:
        headers = ["Comparison", *(f"Option {i}" for i in range(1, len(handoff.options) + 1))]
        rows = [headers, ["---"] * len(headers)]
        rows.extend([row.label, *row.values] for row in handoff.comparison)
        parts.append("\n".join("| " + " | ".join(map(_cell, row)) + " |" for row in rows))
    for index, option in enumerate(handoff.options, 1):
        parts.extend(
            [
                f"### Option {index}: {option.name}",
                "**You get**",
                _bullets(option.you_get),
                "**Changes**",
                _bullets(option.changes),
                "**Watch out**",
                _bullets(option.watch_out),
            ]
        )
        if index == handoff.recommendation:
            name = scope_name(number, index)
            parts.append(f"**Implementation scope:** [{name}]({links[name]})")
    trigger_rows = ["| Trigger | Fired | Evidence |", "| --- | --- | --- |"]
    trigger_rows.extend(
        f"| {TRIGGER_LABELS[check.name]} | {'yes' if check.fired else 'no'} | "
        f"{_cell(check.evidence)} |"
        for check in handoff.triggers
    )
    parts.extend(
        [
            "### Recommendation",
            f"Option {handoff.recommendation}: {handoff.reason}",
            "### Trigger check",
            "\n".join(trigger_rows),
        ]
    )
    if handoff.fired:
        parts.append("> [!TIP]\n> Reply with the option number to select its implementation scope.")
    return "\n\n".join(parts) + "\n"


def render_details(
    number: int, handoff: Handoff, links: Mapping[str, str], *, automatic: bool = False
) -> str:
    name = f"{number}-code-findings.md"
    conversation = f"{number}-conversation.md"
    extra = (
        f"**Conversation:** [{conversation}]({links[conversation]})"
        + (" (automatic)" if automatic else "")
        + "\n\n"
        if conversation in links
        else ""
    )
    return (
        f"## Details\n\n**Code findings:** [{name}]({links[name]})\n\n"
        f"{extra}**Key points**\n{_bullets(handoff.key_points)}\n"
    )


def render_scope_text(title: str, scope: Scope) -> str:
    """The implementation scope: allowed files first, then only what the agent needs."""
    files = _bullets([_path(item) for item in scope.allowed_files])
    sections = [
        ("Allowed files", f"- You may touch only these files (create them if new):\n{files}"),
        ("Source of truth", _bullets(scope.source_of_truth)),
        ("Changes", _bullets(scope.file_changes)),
        (
            "In-scope scenarios",
            "\n".join(f"{i}. {_scenario(item)}" for i, item in enumerate(scope.in_scope, 1)),
        ),
        (
            "Out-of-scope scenarios",
            "\n".join(f"{i}. {_scenario(item)}" for i, item in enumerate(scope.out_of_scope, 1)),
        ),
        ("Do not change", _bullets(scope.do_not_change)),
        ("Verification", _bullets(scope.verification)),
        ("Final message", _bullets(scope.final_message)),
        (ORCHESTRATOR_NOTES_HEADING, _bullets(scope.orchestrator_notes)),
    ]
    parts = [f"# {title}"]
    for heading, content in sections:
        if content:
            parts.extend([f"## {heading}", content])
    return "\n\n".join(parts) + "\n"


def scope_name(number: int, index: int) -> str:
    """The attachment name of option `index`'s implementation scope."""
    return f"{number}-solution-{index}.md"


def render_scope(number: int, index: int, name: str, scope: Scope) -> str:
    return render_scope_text(f"{number} solution {index}: {name}", scope)


def render_attachments(
    number: int, handoff: Handoff, conversation: str | None = None
) -> dict[str, str]:
    return {
        **({f"{number}-conversation.md": conversation} if conversation is not None else {}),
        f"{number}-code-findings.md": handoff.findings.rstrip() + "\n",
        scope_name(number, handoff.recommendation): render_scope(
            number,
            handoff.recommendation,
            handoff.options[handoff.recommendation - 1].name,
            handoff.scope,
        ),
    }


def render_body(sections: Mapping[str, str], analysis: str, details: str) -> str:
    """Preserve Summary, Scope and Solution while publishing analysis and findings."""
    return (
        "\n\n".join(
            (analysis if name == "Analysis" else details).strip()
            if name in {"Analysis", "Details"}
            else f"## {name}\n\n{sections.get(name, '').strip()}"
            for name in ("Summary", "Scope", "Analysis", "Solution", "Details")
        ).rstrip()
        + "\n"
    )
