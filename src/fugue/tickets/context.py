"""Bounded, grounded context for the analyst, gathered without an agent."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from fugue.overview import Overview, build_facts_text
from fugue.overview.summary import END, MAX_FACTS
from fugue.store.records import UsageTotals

MAX_RELATED = 5
MIN_SCORE = 0.2
_WORD = re.compile(r"[a-z0-9]+")
_CITATION = re.compile(r"#(\d+)")
_STOP_WORDS = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "not",
        "but",
        "you",
        "your",
        "our",
        "can",
        "will",
        "would",
        "should",
        "could",
        "into",
        "onto",
        "than",
        "then",
        "them",
        "they",
        "their",
        "there",
        "here",
        "when",
        "where",
        "which",
        "what",
        "who",
        "how",
        "why",
        "all",
        "any",
        "out",
        "its",
        "off",
        "via",
        "per",
    ]
)


class Titled(Protocol):
    @property
    def number(self) -> int: ...

    @property
    def title(self) -> str: ...


@dataclass(frozen=True)
class Related:
    number: int
    title: str
    state: str
    score: float


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) >= 3 and w not in _STOP_WORDS}


def related_candidates(
    idea: str, tickets: Sequence[Titled], closed: Sequence[Titled]
) -> list[Related]:
    """Up to five open or closed issues sharing words with the idea, best first.

    The score is the Jaccard overlap of lower-cased word tokens of length 3 or more
    without stop words; below 0.2 an issue is not shown.
    """
    wanted = _tokens(idea)
    if not wanted:
        return []
    found: dict[int, Related] = {}
    for state, items in (("closed", closed), ("open", tickets)):
        for item in items:
            have = _tokens(item.title)
            union = wanted | have
            score = len(wanted & have) / len(union) if union else 0.0
            if score >= MIN_SCORE:
                found[item.number] = Related(item.number, item.title, state, score)
    ranked = sorted(found.values(), key=lambda r: (-r.score, r.number))
    return ranked[:MAX_RELATED]


def _title(text: str) -> str:
    cleaned = " ".join(text.replace("#", "").split())
    return cleaned if len(cleaned) <= 80 else cleaned[:77] + "..."


def _extend(base: str, sections: Sequence[tuple[str, Sequence[str]]]) -> str:
    """Add sections before END while the whole text stays within MAX_FACTS characters."""
    lines = base.splitlines()
    if lines and lines[-1] == END:
        lines.pop()
    used = sum(len(line) + 1 for line in lines)
    budget = MAX_FACTS - len(END) - 1
    for title, items in sections:
        extra = [f"{title}: none"] if not items else [f"{title}:", *items]
        for line in extra:
            if used + len(line) + 1 > budget:
                break
            lines.append(line)
            used += len(line) + 1
    lines.append(END)
    return "\n".join(lines)


def build_facts(overview: Overview, candidates: Sequence[Related]) -> str:
    """The overview facts and the related candidates between BEGIN FACTS and END FACTS."""
    related = [f"#{c.number} {c.state}: {_title(c.title)}" for c in candidates]
    return _extend(build_facts_text(overview), [("related work", related)])


def suggest_facts(overview: Overview, usage: Sequence[UsageTotals]) -> str:
    """Open tickets, unfinished runs and reported usage totals for the Suggest start."""
    totals = [
        f"ticket {u.key}: {u.records} records, input tokens {u.input_tokens}, "
        f"output tokens {u.output_tokens}, duration ms {u.duration_ms}"
        for u in usage
    ]
    return _extend(build_facts_text(overview), [("reported usage totals", totals)])


def grounded(text: str, facts: str) -> bool:
    """False when text cites a `#N` that the facts do not contain."""
    known = {int(n) for n in _CITATION.findall(facts)}
    return {int(n) for n in _CITATION.findall(text)} <= known
