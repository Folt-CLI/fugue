# Working templates

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](../process.md#autonomous-development-campaign-exceptions) for the campaign.

Record campaign technical decisions as **autonomous orchestrator decisions**.

Use these templates to work a ticket by size and issue type. They record the
Fugue policy decided in [Question #2](https://github.com/Folt-CLI/fugue/issues/2), Option 1.

Reading order: size file first, then the type row.

| Size | Analysis | Review | Components | Typically |
| --- | --- | --- | --- | --- |
| [XS](xs.md) | Full, in an analysis batch | No | Exactly one | Smallest, self-descriptive work item |
| [S](s.md) | Full, in an analysis batch | No | Exactly one | Straightforward ticket whose decision is already made |
| [M](m.md) | Full, by its own agent | Yes | Up to two | Work that may leave several solutions open, or a small Question; sits under an L or an XL |
| [L](l-question.md) | Interactive | No | Several; each represented by its own child | Always a Question, split into M/S/XS children |
| [XL](xl-epic.md) | None | No | None; Components belong on children | Always an Epic; a release that only groups work |

Use M when a ticket is still loose and can be read several ways: it needs full
analysis, and the developer is asked only when a
[trigger](#analysis-triggers-and-re-sizing) fires. If the decision is already
made and only the files and scenarios remain open, use S or XS, not M.

An M Question is a Question that needs no interview: its analysis runs on its
own, its answer is recorded, and its children are created without a further
request. It may sit under an XL when it is a small question or feature, or
under an L when it covers something the L did not, or a small question the L
left open on purpose to answer during its development. A Question that hits a
trigger parks, and an architectural trigger re-sizes it to L.

## Shared steps

- Ticket work in the repository where it happens. Reference other repositories
  by full name. Use native parent links for grouping and dependency links for
  real prerequisites. Derived tickets belong under the Question whose answer they come from;
  otherwise use an existing delivery group or release epic #1 unless directed otherwise.
- Give every ticket a milestone (the milestone of the release it is for, currently `0.1.0`; otherwise `Backlog`),
  a Priority (`Urgent`, `High`, `Medium`, `Low`), a Size and a Status. Follow
  [ticket planning](../ticket-planning.md) for assignment and project membership.
  Give every pull request a milestone, an assignee and the project.
- Use issue types: Task, Bug, Feature, Question, Epic, Maintenance, Documentation,
  Incident, Test. Issue types replace Type labels; Component and Disposition
  are issue fields replacing those labels. Set Components after the solution is
  selected.
- Use exactly Summary, Scope, Analysis, Solution and Details for work-item bodies
  as in [Issue format](../issue-format.md); fill Summary and Scope at creation.
  Follow the size file for analysis and solution readiness, and the Incident
  and Epic rows below for their report and Objective formats.
- For XS, S, M and Questions, the orchestrator writes Analysis inline, attaches the
  findings as `<N>-code-findings.md` and the selected option's implementation
  scope as `<N>-solution-<k>.md`. Options stay inline and have no files until one
  is selected. Follow
  [Analysis and solution selection](../issue-format.md#analysis-and-solution-selection)
  for the layouts, uploads and selection. XL has no analysis.
  XS and S are created with the outcome, numbered scenarios and the constraints the parent Question
  decided, and no scope attachment; their full analysis, shared in an [analysis batch](#analysis-depths), fills Analysis and Solution. See [XS and S body layout](../issue-format.md#xs-and-s-body-layout).
- Use these Status values: `S0: To Do`, `S1: In Analysis`,
  `S3: Ready For Implementation`, `S4: In Implementation`, `S5: Done`.
  XS and S are ready to analyse when the body has its outcome, numbered scenarios and the
  constraints from the parent Question. XS, S, M and Questions need a recorded selection
  before S3, made by the developer or by
  [policy](#analysis-triggers-and-re-sizing): mark the attachment
  **[Selected Solution]** and fill Solution. Their path is S0 -> S1 -> S3 -> S4 -> S5; an XS or S that already carries its scope attachment goes S0 -> S4 -> S5.
  Do not start the implementation agent or move a ticket to S3 or S4 before these requirements are met.
  Project workflows set Done on close and on merge.
- The implementation agent implements and fixes in a dedicated worktree and does not analyse code,
  reviews or CI. The orchestrator analyses, runs the gate, commits, pushes and
  opens the PR. Only the developer merges. Follow [Testing](../testing.md) for
  roles, tests and the gate, and [Review](../review.md) for read-only
  review of M.
- Ask the developer only on Question tickets and when a
  [trigger](#analysis-triggers-and-re-sizing) parks a ticket. Otherwise use the
  ticket and the analysis recommendation, state assumptions in the result and
  continue. Hard-to-reverse or outward-facing actions need confirmation unless
  already permitted; creating the children of an M Question is permitted.
- Background sub-agents unblock the orchestrator: move ticket Status,
  project fields, links, Disposition; watch the implementation agent or CI to completion and
  report. No code/review/CI analysis or decisions.
  Analysis agents are read-only and produce drafts as files: findings, options,
  a recommendation and the selected scope. The orchestrator publishes them to the ticket and
  attaches them.
  The orchestrator reviews the diff, runs the gate, commits, pushes, opens the
  PR; only the developer merges.
- Copy the issue's type into commit subjects: `<Issue Type>[#N]: ...`.
  Follow [Workflow](../workflow.md) for commit bodies and PR delivery, and
  [Stacked pull requests](../stacked-prs.md) for dependent branches and PRs.
- Record Disposition before closing. Use `Merged` only for work delivered by a
  merged pull request; for any other closure, use the matching Disposition and
  record the reason before setting `S5: Done`. A superseded child ticket keeps
  its parent and closes with `Disposition: Superseded`. Refine the Size when
  analysis clarifies the scope; the new Size sets the review and
  Component limits. See [Re-sizing](#analysis-triggers-and-re-sizing). A parent cannot close until all its
  children are closed. It may reach `S5: Done` with its Disposition beforehand,
  such as an answered Question with open children. Do not rely on the project
  workflow "Auto-close issue" for parents. Record delivery and verification in the PR.

## Analysis depths

- **Full analysis** (XS, S, M, M Question): records findings, offers one or more options
  with their trade-offs, recommends one and checks the triggers. When no trigger
  fires, the recommendation is selected by policy and the selected option's scope
  is written. One option is enough when one approach is clearly right; do not
  invent a second one. Only the recommended option has a scope until another is
  selected, and selecting another scopes it first.
- **Analysis batch** (XS, S): one analyst call analyses up to `limits.analysis_batch_size` (default 5) ready XS and S tickets of a parent run together. Each ticket gets a complete full analysis of the same shape and depth as an M's, never an abbreviated one: no review follows XS and S, so their analysis is the safeguard. Each ticket's handoff is validated on its own; a missing or invalid one is analysed again alone, and the others are kept. Each ticket is then published, checked for triggers and selected on its own; a fired trigger parks only that ticket. M and L are always analysed by their own agent, one per ticket.
- **Just in time**: a ticket is analysed only when it is ready, with no open blocker: a sibling blocker must be merged into the feature branch and an outside blocker closed. Batches are formed from the ready XS and S tickets by Priority, then ticket number; when merges make more tickets ready, the next batch is formed from them. An M, and an M Question, waits the same way.
- **Scoping**: the configurable `scope` depth, not a default. It decides nothing. It reads the ticket, the parent Question's
  findings and the current code, and returns the allowed files (including the
  test files), the numbered scenarios and the verification commands. The result
  goes to the implementation agent and is not attached to the ticket. If the ticket does not fit the code
  or leaves a design question open, scoping stops and the ticket is re-sized to M.
- **Interview** (L Question): an interactive analysis with the developer.

## Analysis triggers and re-sizing

A trigger is an observable reason to ask the developer. The analysis states for
each trigger whether it fired and the evidence in one line; a confidence score is
not a trigger.

1. Requirements are materially ambiguous.
2. The alternatives produce meaningfully different user-visible behaviour.
3. The decision creates a significant architectural commitment.
4. The change is destructive or hard to reverse.

When no trigger fires, the recommendation is selected by policy: the selection
and its reason are recorded as made by policy, not by an agent, and the ticket
moves on. When a trigger fires, the ticket parks at its current Status with the
trigger shown, and the developer selects. A fired architectural trigger also
re-sizes an M to an L, so the decision reaches [the stack](../stack.md) through
a Question.

Re-size when new evidence warrants it, and record the old and new Size on the
ticket: XS to S when implementation needs more than the ticket says; S to M when
analysis finds an open design question, the run is blocked outside its allowed
files or the repair limit is reached; M to L on the architectural trigger. Never
lower a Size because a diff is small. A change in a risky area is not XS merely
because it is short. The Size rules are set by `policy.size_rules`; see
[configuration](../../user/configuration.md#repository-policy).

## By issue type

| Type | What changes in the steps | Commit subject |
| --- | --- | --- |
| Task | Describe the bounded work in Summary and Scope; follow the size steps. | `Task[#N]: ...` |
| Bug | Record reproduction steps, actual and expected behaviour; add a regression test that fails before the fix. | `Bug[#N]: ...` |
| Feature | Describe the user need and intended result; follow the size steps. | `Feature[#N]: ...` |
| Maintenance | Describe the maintenance outcome and scope; follow the size steps. | `Maintenance[#N]: ...` |
| Documentation | Describe the documentation change; every PR runs the full required CI check. | `Documentation[#N]: ...` |
| Test | The whole outcome is test cases; see [Testing](../testing.md#test-tickets). Tests belonging to another change stay in that change's ticket. | `Test[#N]: ...` |
| Question | Record a decision. An L has it selected by the developer; an M Question has it selected by policy unless a trigger fires. Derive children from the answer; write each child's outcome, numbered scenarios and **Agreed in #N** constraints at creation, with no scope attachment. Close with `Disposition: Answered` only after all children are closed; Done with Answered may precede closure. | `Question[#N]: ...` |
| Epic | Use only `## Objective` in the body. Follow XL: title `<version> - Fugue`, group release work, close with `Disposition: Released` after all children close. | `Epic[#N]: ...` |
| Incident | Follow [Incidents](../incidents.md) for evidence, impact, linked response tickets and resolution; the report is not itself the fix. | `Incident[#N]: ...` |

## Rules held elsewhere

- [Ticket planning](../ticket-planning.md) holds priority and relationship rules.
- [Workflow](../workflow.md) holds commit and pull request rules.

## Existing guides

Use [Process](../process.md) and [AGENTS.md](../../../AGENTS.md) as entry points.
Consult [Workflow](../workflow.md), [Ticket planning](../ticket-planning.md),
[Issue format](../issue-format.md), [Testing](../testing.md),
[Review](../review.md), [Incidents](../incidents.md) and
[Stacked pull requests](../stacked-prs.md) for their specific procedures.

## Fugue policy

- XS, S, M and L: S0 -> S1 -> S3 -> S4 -> S5. An XS or S that already carries its scope: S0 -> S4 -> S5.
- XL: S0 -> S4 -> S5. Move to S4 when the first child enters S4.
  XL never uses S1 or S3 and has no Component; set Components on children only.
