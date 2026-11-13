## When it applies

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](../process.md#autonomous-development-campaign-exceptions) for the campaign.

Use M when a ticket is still loose and can be read several ways. It needs
analysis and review, and the developer selects a solution. If there is one
straightforward solution, use S or XS, not M.

Split it into M/S items when warranted.

## The ticket must contain

Use the five-section [work-item body](../issue-format.md) with Summary and Scope filled at creation.
Set the milestone, Priority, Size, Status and ownership from the [shared steps](README.md#shared-steps).

## Analysis and review

M needs analysis AND review. Analysis agents are read-only and produce drafts
as files: findings, solution scopes and Analysis text. The orchestrator publishes
them to the ticket and attaches them.

The orchestrator writes Analysis inline, links the findings attachment from
Details and attaches each solution as a complete implementation scope.

Follow [Analysis and solution selection](../issue-format.md#analysis-and-solution-selection)
for the layouts, uploads and selection.
Use a read-only [review](../review.md).

## Solution

The developer selects the solution before implementation. Mark its implementation scope
attachment **[Selected Solution]**.
Summarize changes, Affected areas and Why this one in Solution; link findings and two to four key points in Details.

## Statuses

Status path: `S0: To Do` -> `S1: In Analysis` -> `S3: Ready For Implementation` -> `S4: In Implementation` -> `S5: Done`.

S3 requires a recorded selected solution: mark the attachment
**[Selected Solution]** and fill Solution. Do not start the implementation agent or move the ticket
to S3 or S4 before this is recorded.

Project workflows set Done on close and on merge.

## Who does what

The implementation agent implements and fixes in a dedicated worktree and does not analyse; the orchestrator analyses.
The orchestrator runs the [gate](../testing.md#gate-before-push), commits, pushes and opens the PR; only the developer merges.
Give every PR a milestone, an assignee and the project. Apply the [type row](README.md#by-issue-type).

## Components and parents

Set up to two Component issue fields, for example Source Code and Tests for unit tests.
Use the [shared parent and repository rules](README.md#shared-steps).

## Closing

Record Disposition and PR verification. A parent cannot close until all children are closed.
It may be `S5: Done` with its Disposition before then; do not rely on "Auto-close issue" for parents.
