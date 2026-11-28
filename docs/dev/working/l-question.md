## When it applies

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](../process.md#autonomous-development-campaign-exceptions) for the campaign.

An L is always a Question. Split it into M/S/XS child tickets under it. An L
carries no solution files: its children carry the work.

## The ticket must contain

Use the five-section [work-item body](../issue-format.md) with Summary and Scope filled at creation.
Set type Question and the milestone, Priority, Size, Status and ownership from the [shared steps](README.md#shared-steps).

## Analysis and review

L is analysed with the developer in an interview and has no review: it changes no
code, and its children are reviewed by their own size. The orchestrator writes
Analysis inline in the ticket and links one findings attachment from Details; the
findings are the shared context of the children. Options stay inline. Follow
[Analysis and solution selection](../issue-format.md#analysis-and-solution-selection)
for the layouts, uploads and selection.

## Solution

The developer selects the answer. Record the selection and its reason once in
Solution, summarize changes, Affected areas and Why this one, and link findings
and two to four key points in Details. State each decision as a constraint a
child can quote under **Agreed in #N**. Derive the child tickets from the answer.
Each child's body holds its outcome, numbered scenarios and **Agreed in #N**
constraints; it carries no scope attachment.

## Statuses

Status path: `S0: To Do` -> `S1: In Analysis` -> `S3: Ready For Implementation` -> `S4: In Implementation` -> `S5: Done`.
Project workflows set Done on close and on merge. An answered Question may be Done with Answered while its children remain open.

## Who does what

The orchestrator analyses; the developer selects the answer. The implementation agent implements and fixes in dedicated child worktrees and does not analyse.
The orchestrator runs the [gate](../testing.md#gate-before-push), commits, pushes and opens PRs; only the developer merges.
Give every PR a milestone, an assignee and the project.

## Components and parents

L may name several Components describing what can be done; represent each one by its own child ticket.
Each child keeps its size's Component limit. Use native parents and the [shared repository rules](README.md#shared-steps).

## M Question

An M Question is a Question that needs no interview. It may sit under an XL when
it is a small question or feature, or under an L when it covers something the L
did not, or a small question the L left open on purpose to answer during its
development.

1. The analysis runs on its own once the Question has no open blocker (a sibling blocker merged into the feature branch, an outside blocker closed); until then it waits and is reported as waiting for that blocker. It records findings, options when there is a real
   trade-off, a recommendation and the
   [trigger](README.md#analysis-triggers-and-re-sizing) check.
2. When no trigger fires, the recommendation is selected by policy and recorded
   with its reason. When one fires, the Question parks for the developer, and an
   architectural trigger re-sizes it to L.
3. The children are created without a further request. Record in the Question
   what was created. In a parent run, the children join the parent's work on the
   same feature branch.

`fugue run` and `fugue question` do this for any Question whose Size analyses in
full and selects by policy. An M Question has no review and no implementation of
its own.

## Closing

Close with `Disposition: Answered` only after all children are closed.
It may be `S5: Done` with its Disposition before then; do not rely on "Auto-close issue" for parents.
