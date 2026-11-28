## When it applies

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](../process.md#autonomous-development-campaign-exceptions) for the campaign.

Use XS for the smallest work item; the ticket is self-descriptive.

## The ticket must contain

Use the five-section [work-item body](../issue-format.md) with Summary and Scope filled at creation.
Set the milestone, Priority, Size, Status and ownership from the [shared steps](README.md#shared-steps).

## Analysis and review

XS gets a [full analysis](README.md#analysis-depths), shared with other ready XS and S tickets in an analysis batch, and no review. There is no review behind it, so the analysis is complete, never abbreviated: findings, options, a recommendation, the four trigger checks, key points and the scope of the recommended option.

## Outcome

Write the outcome in Summary and two or three numbered scenarios in Scope, with
**Agreed in #N** when a parent Question decided something the work must respect.
XS is created with no scope attachment; its analysis publishes Analysis, the findings and the recommended option's scope, and the selection fills Solution. When no trigger fired the policy selects; a fired trigger parks the ticket for the developer.

If implementation needs more than the ticket says, stop and re-size to S. See
[Re-sizing](README.md#analysis-triggers-and-re-sizing).

## Statuses

The ticket is ready to analyse when its outcome and scenarios exist and it has no open blocker. It is analysed only then, never on code that does not exist yet.

Status path: `S0: To Do` -> `S1: In Analysis` -> `S3: Ready For Implementation` -> `S4: In Implementation` -> `S5: Done`. An XS that already carries its scope attachment goes `S0: To Do` -> `S4: In Implementation` -> `S5: Done`.
Project workflows set Done on close and on merge.

## Who does what

The implementation agent implements and fixes in a dedicated worktree and does not analyse.
The orchestrator runs the [gate](../testing.md#gate-before-push), commits, pushes and opens the PR; only the developer merges.
Give every PR a milestone, an assignee and the project.

## Components and parents

Set exactly one Component issue field. Use the [shared parent and repository rules](README.md#shared-steps).

## Closing

Record Disposition and PR verification. A parent cannot close until all children are closed.
It may be `S5: Done` with its Disposition before then; do not rely on "Auto-close issue" for parents.
