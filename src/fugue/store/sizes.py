"""How each run's Size held up: the original Size, a re-size, who selected, repairs and reviews.

Everything here is read from what the run already recorded, so it needs no schema change and
works on runs made before it existed (a run without a `resize` record was not re-sized).
"""

import json
from dataclasses import dataclass

from fugue.store.db import Store

RESIZE = "resize"
"""The operation kind recorded when a ticket is re-sized during a run."""


@dataclass(frozen=True)
class SizeRecord:
    run_id: int
    ticket: int
    repo: str
    state: str
    original_size: str | None
    resized_to: str | None
    selection: str
    repairs: int
    review_rounds: int


def size_records(
    store: Store, *, ticket_number: int | None = None, repo: str | None = None
) -> list[SizeRecord]:
    """One record per run, oldest first."""
    records = []
    for run in store.list_runs(ticket_number, repo):
        sizes = [
            json.loads(row[0]).get("size")
            for row in store._connection.execute(
                "SELECT intent_json FROM operations WHERE run_id = ? AND kind = 'model_choice' "
                "ORDER BY id",
                (run.id,),
            )
        ]
        resize = store._connection.execute(
            "SELECT intent_json FROM operations WHERE run_id = ? AND kind = ? ORDER BY id DESC",
            (run.id, RESIZE),
        ).fetchone()
        kinds = {
            row[0]
            for row in store._connection.execute(
                "SELECT DISTINCT kind FROM operations WHERE run_id = ?", (run.id,)
            )
        }
        selection = (
            "policy"
            if "analysis_policy_selection" in kinds
            else "developer"
            if "analysis_body" in kinds
            else "none"
        )
        attempts = store.stage_attempts(run.id)
        records.append(
            SizeRecord(
                run.id,
                run.ticket_number,
                run.repo,
                run.state,
                sizes[0] if sizes else None,
                json.loads(resize[0]).get("to") if resize else None,
                selection,
                sum(1 for a in attempts if a.stage == "repair" and a.status == "completed"),
                sum(1 for a in attempts if a.stage == "review" and a.status == "completed"),
            )
        )
    return records
