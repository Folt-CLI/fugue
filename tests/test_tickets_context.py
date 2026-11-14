"""Grounding context and lexical related-work search, gathered without an agent."""

from datetime import UTC, datetime

from fugue.github.creation import IssueHit
from fugue.overview import Overview, RunRow, TicketRow
from fugue.store.records import UsageTotals
from fugue.tickets.context import (
    Related,
    build_facts,
    grounded,
    related_candidates,
    suggest_facts,
)

NOW = datetime(2026, 5, 1, tzinfo=UTC)


def row(number: int, title: str) -> TicketRow:
    return TicketRow(number, title, "Task", "S", "S0: To Do", "Medium")


def overview(*rows: TicketRow, runs: tuple[RunRow, ...] = ()) -> Overview:
    return Overview(repo="o/r", generated_at=NOW, tickets=rows, runs=runs)


def test_related_candidates_rank_open_and_closed_by_overlap() -> None:
    tickets = [row(7, "Add export command for tickets"), row(8, "Unrelated thing")]
    closed = [IssueHit(5, "Export tickets to CSV", "closed")]
    found = related_candidates("Export tickets as CSV with a command", tickets, closed)
    assert [(c.number, c.state) for c in found] == [(5, "closed"), (7, "open")]
    assert found[0].score > found[1].score >= 0.2


def test_related_candidates_have_at_most_five_and_a_minimum_score() -> None:
    tickets = [row(n, f"export tickets report {n}x") for n in range(1, 10)]
    found = related_candidates("export tickets report", tickets, [])
    assert len(found) == 5
    assert [c.number for c in found] == [1, 2, 3, 4, 5]
    assert related_candidates("export tickets report", [row(1, "Deploy the server now")], []) == []


def test_related_candidates_ignore_stop_words_and_short_words() -> None:
    assert related_candidates("the and for to of", [row(1, "the and for to of")], []) == []
    assert related_candidates("", [row(1, "Anything")], []) == []


def test_an_open_ticket_wins_over_a_closed_hit_with_the_same_number() -> None:
    found = related_candidates(
        "export tickets", [row(3, "export tickets")], [IssueHit(3, "export tickets", "closed")]
    )
    assert [(c.number, c.state) for c in found] == [(3, "open")]


def test_facts_are_bounded_and_marked() -> None:
    rows = [row(n, "A very long title about export " + "word " * 30) for n in range(1, 200)]
    candidates = [Related(n, "export " * 20, "closed", 0.5) for n in range(300, 305)]
    facts = build_facts(overview(*rows), candidates)
    assert len(facts) <= 6000
    assert facts.startswith("BEGIN FACTS")
    assert facts.endswith("END FACTS")


def test_facts_name_related_work_and_strip_hashes_from_titles() -> None:
    facts = build_facts(
        overview(row(7, "Fix #99 export")), [Related(5, "Old #98 export", "closed", 0.4)]
    )
    assert "#7 Task size S" in facts
    assert "related work:" in facts
    assert "#5 closed: Old 98 export" in facts
    assert "#99" not in facts and "#98" not in facts


def test_grounded_rejects_a_number_not_in_the_facts() -> None:
    facts = build_facts(overview(row(7, "Export")), [])
    assert grounded("This relates to #7.", facts)
    assert grounded("No citations.", facts)
    assert not grounded("See #7 and #999.", facts)


def test_suggest_facts_hold_tickets_run_states_and_usage_totals() -> None:
    runs = (RunRow(id=3, ticket_number=7, state="implementing", age_seconds=5.0),)
    usage = [UsageTotals("7", 2, 100, None, 50, None, None, 1000)]
    facts = suggest_facts(overview(row(7, "Export"), runs=runs), usage)
    assert "#7 Task" in facts
    assert "run 3 for ticket #7 is implementing" in facts
    assert "reported usage totals:" in facts
    assert "ticket 7: 2 records, input tokens 100, output tokens 50, duration ms 1000" in facts
    assert len(facts) <= 6000
    assert facts.endswith("END FACTS")
