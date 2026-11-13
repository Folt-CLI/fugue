# Working templates

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](../process.md#autonomous-development-campaign-exceptions) for the campaign.

Record campaign technical decisions as **autonomous orchestrator decisions**.

Use these templates to work a ticket by size and issue type. They record the
Fugue policy decided in [Question #2](https://github.com/Folt-CLI/fugue/issues/2), Option 1.

Reading order: size file first, then the type row.

| Size | Analysis | Review | Components | Typically |
| --- | --- | --- | --- | --- |
| [XS](xs.md) | No | No | Exactly one | Smallest, self-descriptive work item |
| [S](s.md) | No | Yes | Exactly one | Straightforward ticket with its solution already decided |
| [M](m.md) | Yes | Yes | Up to two | Work that may leave several solutions open; split into M/S items when warranted |
| [L](l-question.md) | Yes | Yes | Several; each represented by its own child | Always a Question, split into M/S/XS children |
| [XL](xl-epic.md) | No | No | None; Components belong on children | Always an Epic; a release that only groups work |

Use M when a ticket is still loose and can be read several ways: it needs
analysis and the developer selects a solution. If there is one straightforward
solution, use S or XS, not M.

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
  are issue fields replacing those labels. Set Components after solution selection.
- Use exactly Summary, Scope, Analysis, Solution and Details for work-item bodies
  as in [Issue format](../issue-format.md); fill Summary and Scope at creation.
  Follow the size file for analysis and solution readiness, and the Incident
  and Epic rows below for their report and Objective formats.
- For M and L, the orchestrator writes Analysis inline and attaches findings as
  `<N>-code-findings.md` and each solution as a complete implementation scope
  `<N>-solution-<k>.md`. Follow
  [Analysis and solution selection](../issue-format.md#analysis-and-solution-selection)
  for the layouts, uploads and selection. XS, S and XL have no analysis.
  S and XS carry their Solution at creation, derived from the enabling Question;
  its answer does not replace a missing Solution or scope.
  S always has a complete `<N>-solution-1.md` implementation scope attachment; XS may skip
  it when self-descriptive. Attach with gh-attach and verify by downloading and
  comparing at creation; see [S and XS Solution layout](../issue-format.md#s-and-xs-solution-layout).
- Use these Status values: `S0: To Do`, `S1: In Analysis`,
  `S3: Ready For Implementation`, `S4: In Implementation`, `S5: Done`.
  XS and S require their Solution and scope at creation before S4; only
  self-descriptive XS may skip the scope. Their path is S0 -> S4 -> S5.
  M and L require a recorded selected solution before S3: mark the attachment
  **[Selected Solution]** and fill Solution. Their path is S0 -> S1 -> S3 -> S4 -> S5.
  Do not start the implementation agent or move a ticket to S3 or S4 before these requirements are met.
  Project workflows set Done on close and on merge.
- The implementation agent implements and fixes in a dedicated worktree and does not analyse code,
  reviews or CI. The orchestrator analyses, runs the gate, commits, pushes and
  opens the PR. Only the developer merges. Follow [Testing](../testing.md) for
  roles, tests and the gate, and [Review](../review.md) for read-only
  review where the size requires review.
- Ask the developer only on Question tickets: nothing is known yet; scope work
  items. Otherwise use the ticket and analysis options, state assumptions in the
  result and continue; for M/L the developer selects an option. Hard-to-reverse
  or outward-facing actions need confirmation unless already permitted.
- Background sub-agents unblock the orchestrator: move ticket Status,
  project fields, links, Disposition; watch the implementation agent or CI to completion and
  report. No code/review/CI analysis or decisions.
  Analysis agents are read-only and produce drafts as files: findings, solution
  scopes and Analysis text. The orchestrator publishes them to the ticket and
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
  analysis clarifies the scope; the new Size sets the review and Component
  limits. A parent cannot close until all its
  children are closed. It may reach `S5: Done` with its Disposition beforehand,
  such as an answered Question with open children. Do not rely on the project
  workflow "Auto-close issue" for parents. Record delivery and verification in the PR.

## By issue type

| Type | What changes in the steps | Commit subject |
| --- | --- | --- |
| Task | Describe the bounded work in Summary and Scope; follow the size steps. | `Task[#N]: ...` |
| Bug | Record reproduction steps, actual and expected behaviour; add a regression test that fails before the fix. | `Bug[#N]: ...` |
| Feature | Describe the user need and intended result; follow the size steps. | `Feature[#N]: ...` |
| Maintenance | Describe the maintenance outcome and scope; follow the size steps. | `Maintenance[#N]: ...` |
| Documentation | Describe the documentation change; every PR runs the full required CI check. | `Documentation[#N]: ...` |
| Test | The whole outcome is test cases; see [Testing](../testing.md#test-tickets). Tests belonging to another change stay in that change's ticket. | `Test[#N]: ...` |
| Question | Record a decision selected by the developer. Derive children from the answer; write each S/XS child's Solution and required scope at creation, as decided in this Question. The answer does not replace a missing Solution or scope. Close with `Disposition: Answered` only after all children are closed; Done with Answered may precede closure. | `Question[#N]: ...` |
| Epic | Use only `## Objective` in the body. Follow XL: title `<version> -- Fugue`, group release work, close with `Disposition: Released` after all children close. | `Epic[#N]: ...` |
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

- XS and S: S0 -> S4 -> S5.
- M and L: S0 -> S1 -> S3 -> S4 -> S5.
- XL: S0 -> S4 -> S5. Move to S4 when the first child enters S4.
  XL never uses S1 or S3 and has no Component; set Components on children only.
