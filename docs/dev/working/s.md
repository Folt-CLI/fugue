## When it applies

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](../process.md#autonomous-development-campaign-exceptions) for the campaign.

Use S for straightforward work whose decision is already made. The ticket asks no questions.

## The ticket must contain

Use the five-section [work-item body](../issue-format.md) with Summary and Scope filled at creation.
Set the milestone, Priority, Size, Status and ownership from the [shared steps](README.md#shared-steps).

## Analysis and review

S gets a [full analysis](README.md#analysis-depths), shared with other ready XS and S tickets in an analysis batch, and needs no review. The analysis is complete, never abbreviated, because no review follows it; the gate and CI are its other checks.

## Outcome

Write the outcome in Summary and two to five numbered scenarios in Scope. Add
**Agreed in #N** with the decisions the parent Question made, as constraints the
work must respect, and rough file hints that do not bind. S is created with no scope attachment.

When the ticket is ready, its analysis publishes Analysis, the findings and the scope of the recommended option, and checks the four triggers. When no trigger fired the policy selects the recommendation; a fired trigger parks only this ticket for the developer. The implementation agent receives the selected scope, as for an M. If the analysis finds the ticket leaves a design question open, re-size to M; see [Re-sizing](README.md#analysis-triggers-and-re-sizing). The `scope` analysis depth, [scoping](README.md#analysis-depths), stays available in configuration but is not the default.

## Statuses

The ticket is ready to analyse when its outcome, scenarios and constraints exist and it has no open blocker. It is analysed only then, never on code that does not exist yet.

Status path: `S0: To Do` -> `S1: In Analysis` -> `S3: Ready For Implementation` -> `S4: In Implementation` -> `S5: Done`. An S that already carries its scope attachment goes `S0: To Do` -> `S4: In Implementation` -> `S5: Done`.
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
