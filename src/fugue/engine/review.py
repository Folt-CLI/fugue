"""Review findings: the reviewer prompt, output parsing and the GitHub review to post."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from fugue.github.client import ReviewComment

Severity = Literal["High", "Medium", "Low"]
Outcome = Literal["accepted", "rejected", "deferred"]

MAX_BLOCKING = 3
MAX_NON_BLOCKING = 5
CLEAN_TEXT = "No findings."
MAX_QUOTED = 1000

REVIEW_CONTRACT = """\
Report two lists, Blocking and Non-blocking, judged against the scope and its scenarios.

- Blocking: a defect in code changed by the diff, with a concrete trigger reachable in a listed
  In-scope scenario or in ordinary use of the feature, a wrong result that matters (data loss,
  security hole, incorrect output or a crash in normal operation) and reproduction steps with
  inputs. At most three per round, ranked.
- Non-blocking: hardening, rare or pathological timings, speculative misuse and style. At most
  five, one line each. They never block a merge.
- A finding about a scenario under Out-of-scope scenarios is Non-blocking by definition.
- `No findings.` is the correct answer when nothing is blocking and nothing is worth noting.
- Passes: correctness against the scope always; restart and failure safety only when the scope
  lists restart or failure scenarios; security only when the scope touches subprocesses, the
  filesystem, credentials or the network.
- Do not report an accepted, rejected or deferred finding from the rounds log again. A rejected
  finding returns only when you can show that its reason does not hold.
- Do not ask for new tests or dependencies; do review the tests in the diff."""

OUTPUT_FORMAT = """\
Answer with exactly `No findings.` when there is nothing to report. Otherwise answer with:

## Blocking
1. High|Medium|Low: what goes wrong, the trigger, reproduction steps with inputs and the fix when
   clear. `path:line` (or `path:start-end` for a range)

## Non-blocking
- Low: one line. `path:line`

Give `path:line` for every finding that has a location. To propose a replacement for the
located lines, put a fenced ```suggestion block directly under the finding."""


@dataclass(frozen=True)
class Finding:
    index: int
    severity: Severity
    summary: str
    path: str | None
    start_line: int | None
    end_line: int | None
    suggestion: str | None
    blocking: bool

    @property
    def location(self) -> str | None:
        if self.path is None or self.start_line is None:
            return None
        if self.end_line is None or self.end_line == self.start_line:
            return f"{self.path}:{self.start_line}"
        return f"{self.path}:{self.start_line}-{self.end_line}"


class Clean:
    """The reviewer found nothing to report."""

    def __repr__(self) -> str:
        return "CLEAN"


CLEAN = Clean()


@dataclass(frozen=True)
class Decision:
    """Accept a finding, reject it with a reason, or defer a Non-blocking one to a ticket."""

    accept: bool
    reason: str | None = None
    deferred_to: int | None = None


@dataclass(frozen=True)
class RoundEntry:
    round: int
    finding: Finding
    outcome: Outcome
    reason: str | None = None


_SECTION = re.compile(r"[#*\s]*(non[- ]?blocking|blocking)(?:\s+findings)?\s*[:*]*\s*", re.I)
_ITEM = re.compile(r"\s{0,1}(?:\d+[.)]|[-*+])\s+(.*\S)\s*$")
_SEVERITY = re.compile(r"\**(High|Medium|Low)\**\s*[:\-\u2013\u2014]\s*", re.I)
_LOCATION = re.compile(
    r"(?<![A-Za-z0-9_./+\[\]:-])`?"
    r"(?P<path>(?=[A-Za-z0-9_./+\[\]-]*[A-Za-z_./+\[\]-])[A-Za-z0-9_./+\[\]-]+)"
    r":(?P<start>\d+)(?:-(?P<end>\d+))?`?"
)
_NOTHING = frozenset({"none", "n/a", "no findings", "nothing"})


@dataclass
class _Raw:
    blocking: bool
    parts: list[str]
    suggestion: list[str] | None = None
    unparseable: bool = False


def _severity(text: str, blocking: bool) -> tuple[Severity, str]:
    match = _SEVERITY.match(text)
    if match is None:
        return ("Medium" if blocking else "Low"), text
    name = match[1].capitalize()
    severity: Severity = "High" if name == "High" else "Medium" if name == "Medium" else "Low"
    return severity, text[match.end() :]


def _raw_items(text: str) -> tuple[list[_Raw], bool]:
    raws: list[_Raw] = []
    blocking = True
    saw_section = False
    current: _Raw | None = None
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if fence is not None:
            if current is not None and current.unparseable:
                current.parts.append(line)
            if stripped.startswith("```"):
                fence = None
            elif fence == "suggestion" and current is not None:
                assert current.suggestion is not None
                current.suggestion.append(line)
            continue
        if stripped.startswith("```"):
            if current is None:
                current = _Raw(blocking, [line], unparseable=True)
                raws.append(current)
            elif current.unparseable:
                current.parts.append(line)
            fence = "suggestion" if stripped[3:].strip().casefold() == "suggestion" else "other"
            if fence == "suggestion" and current is not None:
                current.suggestion = []
            continue
        section = _SECTION.fullmatch(stripped)
        if section is not None:
            blocking = section[1].casefold().replace(" ", "").replace("-", "") == "blocking"
            saw_section = True
            current = None
            continue
        item = _ITEM.match(line)
        if item is not None:
            current = _Raw(blocking, [item[1]])
            raws.append(current)
        elif current is not None and stripped:
            current.parts.append(stripped)
        elif stripped:
            current = _Raw(blocking, [stripped], unparseable=True)
            raws.append(current)
    return raws, saw_section


def _unparseable(text: str) -> list[Finding]:
    quoted = text.strip()[:MAX_QUOTED] or "(no output)"
    summary = "The reviewer output could not be parsed as findings. It said: " + quoted
    return [Finding(1, "Medium", summary, None, None, None, None, True)]


def parse_findings(text: str) -> list[Finding] | Clean:
    """Parse reviewer output; an exact `No findings.` is clean, unparseable text is one finding."""
    if text.strip() == CLEAN_TEXT:
        return CLEAN
    raws, saw_section = _raw_items(text)
    blocking: list[Finding] = []
    non_blocking: list[Finding] = []
    for raw in raws:
        joined = " ".join(raw.parts).strip()
        target = blocking if raw.blocking else non_blocking
        limit = MAX_BLOCKING if raw.blocking else MAX_NON_BLOCKING
        if raw.unparseable:
            [fallback] = _unparseable(joined)
            if len(target) < limit:
                target.append(
                    Finding(0, "Medium", fallback.summary, None, None, None, None, raw.blocking)
                )
            continue
        if joined.casefold().strip(" .") in _NOTHING:
            continue
        severity, rest = _severity(joined, raw.blocking)
        match = _LOCATION.search(rest)
        path = start = end = None
        if match is not None:
            path = match["path"]
            start = int(match["start"])
            end = int(match["end"]) if match["end"] is not None else start
            if end < start:
                start, end = end, start
            rest = rest[: match.start()] + " " + rest[match.end() :]
        summary = " ".join(rest.split()).strip(" -\u2013\u2014:;,()[]`") or joined
        suggestion = "\n".join(raw.suggestion) if raw.suggestion is not None else None
        if len(target) < limit:
            target.append(Finding(0, severity, summary, path, start, end, suggestion, raw.blocking))
    ordered = [*blocking, *non_blocking]
    if not ordered:
        return CLEAN if saw_section and _all_nothing(raws) else _unparseable(text)
    return [
        Finding(
            index,
            finding.severity,
            finding.summary,
            finding.path,
            finding.start_line,
            finding.end_line,
            finding.suggestion,
            finding.blocking,
        )
        for index, finding in enumerate(ordered, start=1)
    ]


def _all_nothing(raws: Sequence[_Raw]) -> bool:
    return all(" ".join(raw.parts).strip().casefold().strip(" .") in _NOTHING for raw in raws)


def validate_decisions(findings: Sequence[Finding], decisions: Mapping[int, Decision]) -> list[str]:
    """Problems with a set of decisions; empty when every finding is decided validly."""
    problems: list[str] = []
    known = {finding.index: finding for finding in findings}
    for index in sorted(set(decisions) - set(known)):
        problems.append(f"Decision for unknown finding {index}")
    for index, finding in known.items():
        decision = decisions.get(index)
        if decision is None:
            problems.append(f"Finding {index} has no decision")
        elif decision.accept:
            if decision.deferred_to is not None:
                problems.append(f"Finding {index} cannot be both accepted and deferred")
        elif decision.deferred_to is not None:
            if finding.blocking:
                problems.append(f"Blocking finding {index} cannot be deferred")
            elif decision.deferred_to <= 0:
                problems.append(f"Finding {index} must defer to a ticket number")
        elif decision.reason is None or not decision.reason.strip():
            problems.append(f"Rejecting finding {index} requires a reason")
    return problems


def review_marker(round_number: int) -> str:
    return f"<!-- fugue:review round={round_number} -->"


def finding_marker(round_number: int, index: int) -> str:
    return f"<!-- fugue:r{round_number}:f{index} -->"


_FINDING_MARKER = re.compile(r"<!-- fugue:r(\d+):f(\d+) -->")


def marked_finding(body: str) -> tuple[int, int] | None:
    """The (round, finding index) a comment body was posted for, if any."""
    match = _FINDING_MARKER.search(body)
    return None if match is None else (int(match[1]), int(match[2]))


@dataclass
class DiffLines:
    """Right-side hunk ranges and added lines of one file."""

    hunks: list[tuple[int, int]]
    added: list[int]


def diff_ranges(diff: str) -> dict[str, DiffLines]:
    """Right-side hunk ranges and added lines, per file, from a unified diff."""
    ranges: dict[str, DiffLines] = {}
    path: str | None = None
    old_left = new_left = 0
    next_line = 0
    for line in diff.splitlines():
        if old_left > 0 or new_left > 0:
            if line.startswith("+"):
                if path is not None:
                    ranges[path].added.append(next_line)
                new_left -= 1
                next_line += 1
            elif line.startswith("-"):
                old_left -= 1
            elif not line.startswith("\\"):
                old_left -= 1
                new_left -= 1
                next_line += 1
            continue
        if line.startswith("+++ "):
            target = line[4:].strip().strip('"')
            path = None if target == "/dev/null" else target.removeprefix("b/")
            continue
        header = re.match(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if header is None or path is None:
            continue
        old_left = int(header[1]) if header[1] is not None else 1
        new_left = int(header[3]) if header[3] is not None else 1
        start = int(header[2])
        next_line = start
        if new_left > 0:
            ranges.setdefault(path, DiffLines([], [])).hunks.append((start, start + new_left - 1))
    return ranges


def _label(finding: Finding) -> str:
    kind = "Blocking" if finding.blocking else "Non-blocking"
    return f"**{finding.severity} ({kind}):** {finding.summary}"


def _nearest(added: Sequence[int], line: int) -> int:
    return min(added, key=lambda candidate: (abs(candidate - line), candidate))


def place_finding(
    finding: Finding, ranges: Mapping[str, DiffLines], round_number: int
) -> ReviewComment | None:
    """Keep locations wholly inside a hunk, including context lines, without a note.

    Otherwise attach to the nearest added line in the same file (ties use the earlier
    line), with a note. Without an added line, put the finding in the review body.
    """
    if finding.path is None or finding.start_line is None or finding.path not in ranges:
        return None
    lines = ranges[finding.path]
    start = finding.start_line
    end = finding.end_line if finding.end_line is not None else start
    marker = finding_marker(round_number, finding.index)
    inside = any(low <= start and end <= high for low, high in lines.hunks)
    if inside:
        body = _label(finding)
        if finding.suggestion is not None:
            body += f"\n\n```suggestion\n{finding.suggestion}\n```"
        return ReviewComment(
            finding.path,
            end,
            f"{body}\n\n{marker}",
            start_line=start if end > start else None,
            start_side="RIGHT" if end > start else None,
        )
    if not lines.added:
        return None
    line = _nearest(lines.added, start)
    body = (
        f"{_label(finding)}\n\nReported at `{finding.location}`, which is not part of the "
        "diff; attached to the nearest changed line."
    )
    if finding.suggestion is not None:
        body += (
            f"\n\nSuggested replacement (not applicable here):\n```text\n{finding.suggestion}\n```"
        )
    return ReviewComment(finding.path, line, f"{body}\n\n{marker}")


@dataclass(frozen=True)
class BuiltReview:
    event: str
    body: str
    comments: tuple[ReviewComment, ...]


def build_review(
    findings: Sequence[Finding],
    ranges: Mapping[str, DiffLines],
    round_number: int,
    *,
    separate_identity: bool,
) -> BuiltReview:
    """One GitHub review for a round: inline comments, the rest in the body and the event.

    Without a separate reviewer identity GitHub forbids approving or requesting changes on the
    author's own pull request, so the event is always COMMENT.
    """
    blocking = sum(1 for finding in findings if finding.blocking)
    event = ("REQUEST_CHANGES" if blocking else "APPROVE") if separate_identity else "COMMENT"
    if not findings:
        return BuiltReview(event, f"{CLEAN_TEXT}\n\n{review_marker(round_number)}", ())
    comments: list[ReviewComment] = []
    in_body: list[str] = []
    for finding in findings:
        comment = place_finding(finding, ranges, round_number)
        if comment is not None:
            comments.append(comment)
            continue
        line = f"- {_label(finding)}"
        if finding.location is not None:
            line += f" (`{finding.location}`, not part of the diff)"
        if finding.suggestion is not None:
            line += f"\n\n```text\n{finding.suggestion}\n```"
        in_body.append(line)
    non_blocking = len(findings) - blocking
    lines = [f"Review round {round_number}: {blocking} Blocking, {non_blocking} Non-blocking."]
    if in_body:
        lines.append("Findings without a changed line:\n\n" + "\n".join(in_body))
    lines.append(review_marker(round_number))
    return BuiltReview(event, "\n\n".join(lines), tuple(comments))


def review_prompt(
    *, scope: str, ci_result: str, rounds: Sequence[RoundEntry], base_ref: str
) -> str:
    """The read-only reviewer's instructions for one round."""
    if rounds:
        log = "\n".join(
            f"- Round {entry.round}, finding {entry.finding.index} ({entry.outcome}): "
            f"{entry.finding.summary}"
            + (f" [{entry.finding.location}]" if entry.finding.location else "")
            + (f" Reason: {entry.reason}" if entry.reason else "")
            for entry in rounds
        )
    else:
        log = "No earlier rounds."
    return (
        "Review the pull request read-only. Compare the diff of HEAD against the merge base "
        f"with `{base_ref}` (for example `git diff $(git merge-base {base_ref} HEAD) HEAD`). "
        "Do not change files, run builds or use the network.\n\n"
        f"## Review contract\n\n{REVIEW_CONTRACT}\n\n"
        f"## Output format\n\n{OUTPUT_FORMAT}\n\n"
        f"## Scope\n\n{scope}\n\n"
        f"## CI result\n\n{ci_result}\n\n"
        f"## Rounds log\n\n{log}\n"
    )


def review_repair_prompt(accepted: Sequence[Finding]) -> str:
    """The implementer's instructions for fixing accepted findings."""
    items: list[str] = []
    for finding in accepted:
        item = f"- {finding.severity}: {finding.summary}"
        if finding.location is not None:
            item += f" ({finding.location})"
        if finding.suggestion is not None:
            item += f"\n  Suggested replacement:\n```\n{finding.suggestion}\n```"
        items.append(item)
    return (
        "Fix the accepted review findings within the allowed scope. No git commit, push or gh.\n\n"
        + "\n".join(items)
    )
