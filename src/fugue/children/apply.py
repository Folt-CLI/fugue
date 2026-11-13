"""Plan and apply child creation with durable mutation intents and results."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fugue.children.schema import ChildrenSpec, ChildSpec
from fugue.config import RepoPolicy
from fugue.engine.facts import selected_solution
from fugue.github import GitHubClient
from fugue.models import IssueType, Size, Status, Ticket
from fugue.store import Store
from fugue.workflow.guards import components_ok, ready_for_implementation


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_body(child: ChildSpec, question: int, scope_link: str = "") -> str:
    """Render the five work-item sections and the S/XS Solution layout."""
    if child.size in (Size.XS, Size.S):
        solution = (
            f"> [!IMPORTANT]\n> {child.decision}\n\n"
            f"**Agreed in #{question}**\n\n{_bullets(child.agreed)}\n\n"
            f"**You get**\n\n{_bullets(child.you_get)}\n\n"
            f"**Changes**\n\n{_bullets(child.changes)}\n\n"
            f"**Not changed**\n\n{_bullets(child.not_changed)}\n\n"
            f"**Watch out**\n\n{_bullets(child.watch_out)}"
        )
        if scope_link:
            solution += f"\n\n**Implementation scope:** {scope_link}"
        analysis = f"No analysis needed. Solution decided in #{question}."
    else:
        analysis = "_Pending._"
        solution = "_Pending._"
    return (
        f"## Summary\n\n{child.summary}\n\n## Scope\n\n{child.scope}\n\n"
        f"## Analysis\n\n{analysis}\n\n## Solution\n\n{solution}\n\n"
        f"## Details\n\nDerived from the decision in #{question}.\n"
    )


@dataclass(frozen=True)
class ChildrenPlan:
    spec_json: str
    scopes: tuple[bytes | None, ...]
    fingerprint: str
    bodies: tuple[str, ...]

    @property
    def spec(self) -> ChildrenSpec:
        return ChildrenSpec.model_validate_json(self.spec_json)


def plan(spec: ChildrenSpec, question: Ticket, policy: RepoPolicy) -> ChildrenPlan:
    """Validate everything locally before allowing any external mutation."""
    spec = ChildrenSpec.model_validate_json(spec.model_dump_json())
    if question.number != spec.question or question.issue_type != IssueType.QUESTION:
        raise ValueError("The parent must be the specified Question")
    if not selected_solution(question):
        raise ValueError("The Question must have an explicitly selected solution")
    scopes: list[bytes | None] = []
    bodies: list[str] = []
    for child in spec.children:
        problems = components_ok(child.size, child.components, policy)
        if not child.components:
            problems.append("Children require assigned Components")
        if child.size == Size.M and child.scope_file is not None:
            problems.append("Size M requires analysis before an implementation scope")
        if child.size in (Size.XS, Size.S):
            problems.extend(
                ready_for_implementation(
                    Ticket(
                        number=1,
                        title=child.title,
                        issue_type=child.issue_type,
                        size=child.size,
                        status=Status.TO_DO,
                        priority=child.priority,
                        components=child.components,
                        body_sections={
                            "Summary": child.summary,
                            "Scope": child.scope,
                            "Solution": child.decision,
                        },
                    ),
                    policy,
                    has_scope_attachment=child.scope_file is not None,
                    selected_solution=True,
                )
            )
        if problems:
            raise ValueError(f"{child.key}: {'; '.join(problems)}")
        content = child.scope_file.read_bytes() if child.scope_file is not None else None
        if content is not None and not content.strip():
            raise ValueError(f"{child.key}: scope file must be non-empty")
        scopes.append(content)
        bodies.append(render_body(child, spec.question))
    spec_json = spec.model_dump_json()
    digest = hashlib.sha256(spec_json.encode())
    for content in scopes:
        digest.update(hashlib.sha256(content).digest() if content is not None else b"none")
    return ChildrenPlan(spec_json, tuple(scopes), digest.hexdigest(), tuple(bodies))


async def apply(
    planned: ChildrenPlan, client: GitHubClient, store: Store, *, dry_run: bool = False
) -> dict[str, int]:
    """Create all children before linking dependencies; resume the same durable plan."""
    if dry_run:
        return {}
    spec = planned.spec
    # A Question has one durable child plan. Refuse changed input on a rerun rather
    # than silently creating a second set of children.
    rows = store._connection.execute(
        "SELECT o.run_id, o.intent_json FROM operations o JOIN runs r ON r.id = o.run_id "
        "WHERE r.repo = ? AND r.ticket_number = ? AND o.kind = 'children_plan'",
        (client.repo, spec.question),
    ).fetchall()
    if rows:
        run_id = int(rows[0]["run_id"])
        if json.loads(rows[0]["intent_json"])["fingerprint"] != planned.fingerprint:
            raise ValueError("The recorded child plan differs from this input")
    else:
        run_id = store.create_run(spec.question, client.repo).id
    record = store.begin_operation(
        run_id, "children_plan", "plan", {"fingerprint": planned.fingerprint}
    )

    async def operation(
        key: str, intent: dict[str, Any], action: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        saved = store.begin_operation(run_id, "children", key, intent)
        if json.loads(saved.intent_json) != intent:
            raise ValueError(f"Recorded operation differs: {key}")
        if saved.status == "done" and saved.result_json is not None:
            result: dict[str, Any] = json.loads(saved.result_json)
            return result
        result = await action()
        store.complete_operation(saved.id, result)
        return result

    numbers: dict[str, int] = {}
    for index, child in enumerate(spec.children):
        marker = f"<!-- fugue:children:{run_id}:{child.key} -->"

        async def create(child: ChildSpec = child, marker: str = marker) -> dict[str, Any]:
            number = await client.find_child(spec.question, marker)
            if number is None:
                number = await client.create_issue(
                    child.title,
                    render_body(child, spec.question) + "\n" + marker,
                    child.issue_type,
                    spec.question,
                    spec.milestone,
                    spec.assignee,
                    add_to_project=False,
                )
            return {"number": number}

        created = await operation(child.key + ":create", {"child": child.key}, create)
        number = int(created["number"])
        numbers[child.key] = number

        async def mutate(
            suffix: str,
            intent: dict[str, Any],
            action: Callable[[], Awaitable[None]],
            *,
            child_key: str = child.key,
            number: int = number,
        ) -> None:
            async def perform() -> dict[str, Any]:
                await action()
                return {}

            await operation(child_key + ":" + suffix, {"number": number, **intent}, perform)

        await mutate("project", {}, partial(client.ensure_project, number))
        await mutate(
            "size", {"size": child.size.value}, partial(client.set_size, number, child.size)
        )
        await mutate("status", {}, partial(client.set_status, number, Status.TO_DO))
        await mutate(
            "priority",
            {"priority": child.priority.value},
            partial(client.set_priority, number, child.priority),
        )
        await mutate(
            "components",
            {"components": [c.value for c in child.components]},
            partial(client.set_components, number, child.components),
        )
        content = planned.scopes[index]
        if content is not None:

            async def upload(content: bytes = content, number: int = number) -> dict[str, Any]:
                with TemporaryDirectory(prefix="fugue-child-") as directory:
                    path = Path(directory) / f"{number}-solution-1.md"
                    path.write_bytes(content)
                    uploaded = await client.upload_attachments([path])
                return {"url": uploaded[0].href, "name": uploaded[0].name}

            attachment = await operation(
                child.key + ":scope", {"sha256": hashlib.sha256(content).hexdigest()}, upload
            )
            body = (
                render_body(child, spec.question, f"[{attachment['name']}]({attachment['url']})")
                + "\n"
                + marker
            )
            await mutate("body", {"body": body}, partial(client.edit_issue_body, number, body))

    for child in spec.children:
        for blocker in child.blocked_by:
            blocker_number = numbers[blocker] if isinstance(blocker, str) else blocker
            number = numbers[child.key]
            if number == blocker_number:
                raise ValueError("A child cannot block itself")

            async def block(number: int = number, blocker: int = blocker_number) -> dict[str, Any]:
                await client.block(number, blocker)
                return {}

            await operation(
                f"{child.key}:block:{blocker_number}",
                {"number": number, "blocker": blocker_number},
                block,
            )
    store.complete_operation(record.id, {"children": numbers})
    return numbers
