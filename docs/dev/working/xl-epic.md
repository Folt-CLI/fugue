## When it applies

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](../process.md#autonomous-development-campaign-exceptions) for the campaign.

XL is a release; it only groups the work and is always an Epic.

## The ticket must contain

Title it `<version> -- Fugue`. The body contains only `## Objective` and a short release explanation.
Set type Epic and the milestone, Priority, Size, Status and ownership from the [shared steps](README.md#shared-steps).

## Analysis and review

XL needs no analysis and no review. Follow each child's size for its analysis and review.

## Solution

Keep detailed work in child tickets; the Epic only groups it.

## Statuses

Status path: `S0: To Do` -> `S4: In Implementation` -> `S5: Done`.
Move to S4 when the first child enters S4. XL never uses S1 or S3.
Project workflows set `S5: Done` on close.

## Who does what

Follow [Testing roles](../testing.md#roles) for children: the implementation agent implements and fixes in a dedicated worktree and does not analyse.
The orchestrator analyses, runs the gate, commits, pushes and opens PRs; only the developer merges.
Give every PR a milestone, an assignee and the project.

## Components and parents

Group work with native child relationships; follow each child's size for Component limits.
Use the [shared repository rules](README.md#shared-steps); XL has no Component. Set Components on children only.

## Closing

Close with `Disposition: Released` only after all children are closed.
A parent may be `S5: Done` with its Disposition before closure; do not rely on "Auto-close issue" for parents.
