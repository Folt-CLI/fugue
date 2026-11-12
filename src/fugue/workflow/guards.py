"""Readiness and delivery guards returning actionable policy problems."""

from collections.abc import Sequence

from fugue.config import RepoPolicy
from fugue.models import Component, Size, Status, Ticket


def components_ok(size: Size, components: Sequence[Component], policy: RepoPolicy) -> list[str]:
    """Check assigned Components; readiness separately requires assignment at S4+."""
    maximum = policy.size_rules[size].max_components
    if maximum is not None and len(components) > maximum:
        return [f"Size {size} allows at most {maximum} Components"]
    return []


def review_required(size: Size, policy: RepoPolicy) -> bool:
    return policy.size_rules[size].review


def ready_for_implementation(
    ticket: Ticket,
    policy: RepoPolicy,
    *,
    has_scope_attachment: bool,
    selected_solution: bool,
) -> list[str]:
    problems: list[str] = []
    for section in ("Summary", "Scope", "Solution"):
        text = ticket.body_sections.get(section, "").strip()
        if not text or (section == "Solution" and text == "_Pending._"):
            problems.append(f"{section} must be non-empty and ready")
    if ticket.size == Size.XL:
        problems.append("Size XL is never implementable")
    elif ticket.size in (Size.XS, Size.S):
        if ticket.status not in (Status.TO_DO, Status.IN_IMPLEMENTATION):
            problems.append("Size XS/S requires Status S0 or S4")
        if ticket.size == Size.S and not has_scope_attachment:
            problems.append("Size S requires a scope attachment")
    else:
        if not selected_solution:
            problems.append("Size M/L requires a selected solution")
        if ticket.status not in (Status.READY_FOR_IMPLEMENTATION, Status.IN_IMPLEMENTATION):
            problems.append("Size M/L requires Status S3 or S4")
    problems.extend(components_ok(ticket.size, ticket.components, policy))
    if (
        ticket.size != Size.XL
        and not ticket.components
        and ticket.status in (Status.IN_IMPLEMENTATION, Status.DONE)
    ):
        problems.append("Components must be set at Status S4 or S5")
    return problems


def can_close(ticket: Ticket, children: Sequence[Ticket], *, disposition_set: bool) -> list[str]:
    """Require the ticket's Disposition and every child's completed Status."""
    problems = [
        f"Child #{child.number} must be closed (Status S5) before closing #{ticket.number}"
        for child in children
        if child.status != Status.DONE
    ]
    if not disposition_set:
        problems.append("Disposition must be set before closure")
    return problems


def commit_type_matches(ticket: Ticket, subject: str) -> bool:
    return subject.startswith(f"{ticket.issue_type}[#{ticket.number}]:")


def merge_allowed(
    *,
    gate_passed: bool,
    ci_green: bool,
    review_clean_or_not_required: bool,
    approval_head_sha: str | None,
    current_head_sha: str,
    unresolved_threads: int,
) -> list[str]:
    problems: list[str] = []
    if not gate_passed:
        problems.append("The gate must pass")
    if not ci_green:
        problems.append("CI must be green")
    if not review_clean_or_not_required:
        problems.append("The required review must be clean")
    if approval_head_sha is None or approval_head_sha != current_head_sha:
        problems.append("Merge approval must match the current head SHA")
    if unresolved_threads != 0:
        problems.append("All review threads must be resolved")
    return problems
