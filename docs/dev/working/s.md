## When it applies

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](../process.md#autonomous-development-campaign-exceptions) for the campaign.

Use S for straightforward work with its solution already decided. The ticket asks no questions.

## The ticket must contain

Use the five-section [work-item body](../issue-format.md) with Summary and Scope filled at creation.
Set the milestone, Priority, Size, Status and ownership from the [shared steps](README.md#shared-steps).

## Analysis and review

S needs review but no analysis. Use a read-only Codex [review](../review.md).

## Solution

Write Solution at creation, short and readable for the developer, in the style
of Analysis. It comes from the enabling Question, with no solution selection;
the Question's answer does not replace a missing Solution or scope.

Use a `> [!IMPORTANT]` decision line, **Agreed in #N** (what the parent Question
decided), **You get**, **Changes**, **Not changed** and **Watch out**, then
**Codex scope** linking the complete `<N>-solution-1.md` scope attachment.

Attach with gh-attach and verify at creation by downloading and comparing.

S always has this scope file. Follow the
[S and XS Solution layout](../issue-format.md#s-and-xs-solution-layout) and the
applicable [type row](README.md#by-issue-type).

## Statuses

The ticket is ready when its Solution and scope exist at creation.
Do not start Codex or move the ticket to S3 or S4 before these requirements are met.

Status path: `S0: To Do` -> `S4: In Implementation` -> `S5: Done`.
Project workflows set Done on close and on merge.

## Who does what

Codex implements and fixes in a dedicated worktree and does not analyse.
The orchestrator runs the [gate](../testing.md#gate-before-push), commits, pushes and opens the PR; only the developer merges.
Give every PR a milestone, an assignee and the project.

## Components and parents

Set exactly one Component issue field. Use the [shared parent and repository rules](README.md#shared-steps).

## Closing

Record Disposition and PR verification. A parent cannot close until all children are closed.
It may be `S5: Done` with its Disposition before then; do not rely on "Auto-close issue" for parents.
