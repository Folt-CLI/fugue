"""Durable merge evidence and closure steps."""

import json
from collections.abc import Awaitable, Callable

from fugue.engine.ports import MergeGitHubPort
from fugue.store import Store


def gate_passed(store: Store, run_id: int, head: str) -> bool:
    row = store._connection.execute(
        "SELECT status FROM operations WHERE run_id = ? AND kind = 'merge_gate' AND key = ?",
        (run_id, head),
    ).fetchone()
    return row is not None and row[0] == "done"


def review_clean(store: Store, run_id: int, head: str) -> bool:
    row = store._connection.execute(
        "SELECT key, status, intent_json FROM operations "
        "WHERE run_id = ? AND kind = 'review' ORDER BY id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if row is None or row[1] != "done":
        return False
    intent = json.loads(row[2])
    if intent["head"] != head:
        return False
    findings = intent["findings"]
    if not findings:
        return True
    if any(finding["blocking"] for finding in findings):
        return False
    decision = store._connection.execute(
        "SELECT status, intent_json FROM operations "
        "WHERE run_id = ? AND kind = 'decision' AND key = ?",
        (run_id, row[0]),
    ).fetchone()
    return (
        decision is not None
        and decision[0] == "done"
        and all(not item["accept"] for item in json.loads(decision[1])["decisions"].values())
    )


async def close_step(
    store: Store,
    run_id: int,
    name: str,
    action: Callable[[], Awaitable[None]],
) -> None:
    """Resume closure without repeating steps whose results were recorded."""
    operation = store.begin_operation(run_id, "closure", name, {"step": name})
    if operation.status == "done":
        return
    await action()
    store.complete_operation(operation.id, {"done": True})


async def close_comment(
    store: Store,
    run_id: int,
    name: str,
    port: MergeGitHubPort,
    number: int,
    body: str,
) -> None:
    """Reconcile a closure comment whose successful post may not have been recorded."""
    marker = f"<!-- fugue:closure run={run_id} step={name} -->"

    async def post() -> None:
        if not any(marker in comment for comment in await port.comments(number)):
            await port.comment(number, f"{body}\n\n{marker}")

    await close_step(store, run_id, name, post)
