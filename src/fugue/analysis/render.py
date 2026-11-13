"""Pure Markdown renderers for analysis and implementation scope attachments."""

import re
from collections.abc import Mapping, Sequence

from fugue.analysis.schema import Handoff, Option


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
        name = f"{number}-solution-{index}.md"
        parts.extend(
            [
                f"### Option {index}: {option.name}",
                "**You get**",
                _bullets(option.you_get),
                "**Changes**",
                _bullets(option.changes),
                "**Watch out**",
                _bullets(option.watch_out),
                f"**Implementation scope:** [{name}]({links[name]})",
            ]
        )
    parts.extend(
        [
            "### Recommendation",
            f"Option {handoff.recommendation}: {handoff.reason}",
            "> [!TIP]\n> Reply with the option number to select its implementation scope.",
        ]
    )
    return "\n\n".join(parts) + "\n"


def render_details(number: int, handoff: Handoff, links: Mapping[str, str]) -> str:
    name = f"{number}-code-findings.md"
    return (
        f"## Details\n\n**Code findings:** [{name}]({links[name]})\n\n"
        f"**Key points**\n{_bullets(handoff.key_points)}\n"
    )


def render_scope(number: int, index: int, option: Option) -> str:
    scope = option.scope
    sections = [
        ("Standing rules", scope.standing_rules),
        ("Source of truth", scope.source_of_truth),
        ("File-by-file changes", scope.file_changes),
        ("In-scope scenarios", scope.in_scope),
        ("Out-of-scope scenarios", scope.out_of_scope),
        ("Do not change", scope.do_not_change),
        ("Verification", scope.verification),
        ("Final message", scope.final_message),
        ("Notes for the orchestrator (not for the implementation agent)", scope.orchestrator_notes),
    ]
    parts = [f"# {number} solution {index}: {option.name}"]
    for heading, items in sections:
        if heading.endswith("scenarios"):
            content = "\n".join(f"{i}. {_scenario(item)}" for i, item in enumerate(items, 1))
        else:
            content = _bullets(items)
        if heading == "Standing rules":
            files = _bullets([_path(item) for item in scope.allowed_files])
            content += f"\n- You may touch only these files (create them if new):\n{files}"
        parts.extend([f"## {heading}", content])
    return "\n\n".join(parts) + "\n"


def render_attachments(number: int, handoff: Handoff) -> dict[str, str]:
    return {
        f"{number}-code-findings.md": handoff.findings.rstrip() + "\n",
        **{
            f"{number}-solution-{index}.md": render_scope(number, index, option)
            for index, option in enumerate(handoff.options, 1)
        },
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
