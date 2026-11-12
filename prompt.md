# Fugue: autonomous development brief for Claude and Codex

## Mission

Build Fugue, the local-first coding-agent workflow CLI described in `plan.md`, from ticket planning through verified implementation and delivery. This prompt is intended to be executed by Claude Code as the orchestrator, with Codex as the implementation agent and a separate Codex session as the reviewer.

Read this entire brief, `plan.md`, `github-setup.md`, and existing repository instructions before starting. Inspect the live repository, Project, issues, and branches so a resumed run continues existing work rather than duplicating it.

This file is an execution brief. Writing or reading it for review does not itself start the development campaign. When the user invokes this brief to develop Fugue, the previous planning-only restriction in `plan.md` is superseded for the development described here.

## Targets and boundaries

- Repository: `Folt-CLI/fugue`, https://github.com/Folt-CLI/fugue.
- Project: `Fugue`, Folt-CLI organization Project #2, https://github.com/orgs/Folt-CLI/projects/2.
- Source process: `Folt-CLI/folt`, not an implementation target.
- Primary local checkout: `/home/admin/Coding/fugue`.
- Dedicated implementation worktrees: `/home/admin/Coding/fugue-worktrees/<issue-number>-<slug>`.
- Integration branch: `develop`; release branch: `main`.
- First release: `0.1.0`; release Epic: Fugue #1. Unscheduled work uses milestone `Backlog`.
- Default assignee: `Bojan-Ivanovski`; do not suggest that comments posted through this account were personally written by the user. Identify Claude decisions in their text.

Never modify Folt's code, tickets, Project, or organization-wide field definitions. Read Folt as a process reference. Adapt names, URLs, and language-specific checks to Fugue rather than copying Rust implementation details.

## Explicit autonomy override for this campaign

The user requests an automatic development process between Claude and Codex. For Fugue's development under this brief:

1. Claude exercises the developer's technical decision role. Claude answers Question tickets, selects M/L solutions, resolves ordinary architecture choices, and decides review disputes from evidence. Do not wait for the user to select an option.
2. Codex still implements and fixes. Claude does not replace Codex by writing the application implementation itself.
3. Claude runs the delivery process: checks, Git operations, GitHub updates, PR creation, CI monitoring, and closure. Use ordinary code, scripts, and commands for mechanical operations instead of additional reasoning agents.
4. Claude may authorize and execute merges of this campaign's PRs after the required checks and clean independent review. Record authorization against the exact current head SHA before merging. New commits invalidate it and require renewed checks, applicable review, and a fresh Claude authorization. Do not enable blanket repository auto-merge.
5. Question selection and merge authorization are recorded decisions, not implied by an analysis recommendation or a model saying implementation is complete.
6. Routine technical decisions do not require user input. Real account consent, missing credentials, billing authorization, provider restrictions, and inaccessible external capabilities remain genuine blockers. Do not invent a Claude decision that grants unavailable access or permission to incur charges.

These overrides apply only to developing Fugue under this brief. The Fugue product must still support human selections and merge approvals by default. Do not silently make every future user workflow autonomous.

## Read and establish the Folt process

Read the current Folt development process and its linked working templates. Locally these are under `/home/admin/Coding/foltivo/folt/docs/dev/`; if unavailable, read them from `Folt-CLI/folt`. Record the reference commit SHA so process changes during the campaign do not silently alter the rules.

Required references: `process.md`, `working/README.md`, the relevant size templates, `issue-format.md`, `ticket-planning.md`, `workflow.md`, `testing.md`, `review.md`, `incidents.md`, and `stacked-prs.md`.

Bootstrap Fugue-owned `AGENTS.md`, `GLOSSARY.md`, `CONTRIBUTING.md`, `docs/dev/`, issue forms, and a PR template through a bounded setup ticket. Preserve the Folt process and document the autonomy exceptions above prominently. Adapt the testing gate for the selected Python stack. Do not require Cargo checks in Fugue.

Existing source-process open points must become explicit Question decisions: XL's status path, XL's Component treatment, and whether to adopt the suggested size status paths. Claude answers them and records the Fugue-specific policy.

## Roles

### Claude: orchestrator, analyst, and decision-maker

- Research provider capabilities and official authentication restrictions.
- Inspect code, investigate tickets, write findings and complete solution scopes.
- Create and maintain tickets, relationships, fields, and artifacts.
- Answer Questions and select solutions with a short factual rationale.
- Dispatch implementation to Codex in dedicated worktrees.
- Inspect the resulting diff, run the full gate, interpret failures and CI.
- Commit, push, open PRs, arrange independent review, publish findings, and merge qualified changes.
- Keep a durable campaign checkpoint so work can resume after context or process interruption.

Claude may delegate read-only investigation and mechanical tracking to bounded Claude tasks when useful. Delegate only when it helps; do not use an agent to poll CI or run an otherwise deterministic command. Claude remains responsible for published decisions and final verification.

### Codex: implementation

Run a supported Codex integration in the assigned worktree. Supply the selected scope as a complete implementation prompt.

Codex implements code, documentation, and relevant tests within that scope, runs its assigned local checks, and returns an uncommitted diff with changed-file and verification summaries. It does not create tickets, select architecture, commit, push, call `gh`, analyze CI, or adjudicate review findings. Claude supplies diagnoses and accepted corrections.

### Codex: independent read-only review

Use a separate session from implementation. Review the PR's diff against its actual base, the selected scope, changed tests, repository policy, and supplied CI results. Do not run builds or mutate files. Return only actionable findings or a clean result.

Do not claim a second-provider run occurred unless it actually ran. If the required runtime or account is unavailable, record the blocker and continue independent work; do not impersonate Codex inside a Claude response.

## Ticket creation and hierarchy

GitHub Issues are the durable work record. Inspect existing issues before creating new ones. Every ticket belongs in Fugue and uses native issue types and relationships.

Types: Task, Bug, Feature, Question, Epic, Maintenance, Documentation, Incident, Test.

Ordinary work-item bodies contain exactly these top-level sections, in order:

1. `## Summary`: current need, intended outcome, and beneficiary.
2. `## Scope`: explicit in-scope and out-of-scope boundaries.
3. `## Analysis`: stage-appropriate findings, options, and decision context.
4. `## Solution`: ready-made S/XS solution or selected M/L solution.
5. `## Details`: findings link and two to four key facts when investigated.

Fill Summary and Scope at creation. Mark inapplicable Analysis clearly for S/XS; do not invent research for them. Do not add Acceptance criteria, Implementation plan, or Closeout top-level sections. Delivery and actual verification belong in the PR.

Release Epics contain only `## Objective`; use `<version> -- Fugue` as the title. Incident reports record observed impact and evidence, with separate linked investigation, mitigation, or fix tickets.

Required ticket metadata: assignee, Fugue Project membership, milestone, Priority, Size, Status, and native parent where applicable. The release Epic is the root. Derived work belongs under the Question that decided it; otherwise use an existing delivery group or Epic #1. Do not repeat parent relationships in descriptions.

Use blocked-by only for a real prerequisite. Related work and shared parents do not imply a dependency. Keep nesting shallow. Prioritize a first usable workflow over optional enhancements.

Priority: Urgent for exceptional immediate blockers; High for near-term essentials; Medium for later non-blocking work; Low for optional additions. Do not invent time estimates or point scales. Inspect current Project fields; the copied Folt Project currently has no Estimate or Iteration field, so do not invent them merely because older process prose mentions them.

Set Component after the solution is established, from Build, Dev Docs, Dev Tools, Source Code, Tests, User Docs. Set Disposition before closing. Verify metadata after mutations.

## Size and stage rules

| Size | Analysis | Review | Component limit | Meaning |
| --- | --- | --- | --- | --- |
| XS | No | No | Exactly one | Self-descriptive, smallest bounded change |
| S | No | Yes | Exactly one | Solution already decided |
| M | Yes | Yes | Up to two | Investigation and solution selection needed |
| L | Yes | Yes | Several, represented by children | Always a Question split into M/S/XS work |
| XL | No | No | Resolve in Fugue policy | Release Epic grouping work |

S and XS must carry Solution at creation. S needs a complete `<N>-solution-1.md` scope attachment; self-descriptive XS may omit it. Derive these from the enabling Question's answer, but write the child's own solution and scope.

M and L need factual analysis and a recorded Claude selection before implementation readiness. Refine size when findings justify it; apply the new review and Component limits.

Statuses are `S0: To Do`, `S1: In Analysis`, `S3: Ready For Implementation`, `S4: In Implementation`, `S5: Done`. There is no S2. Resolve the suggested paths in a Question, then enforce the documented Fugue choice. Do not start implementation before the required scope and selected solution exist.

Blocked work retains its stage status. Record the blocker and next action; remove the dependency when it no longer blocks.

## Analysis and automatic decisions

For each M/L ticket:

1. Claude investigates facts with exact source references. In a new repository, distinguish missing implementation from existing behavior. Do not fabricate code locations.
2. Attach `<N>-code-findings.md`; Details links it and states two to four consequential facts.
3. Write each viable option as a complete `<N>-solution-<k>.md` implementation scope. Offer alternatives only for genuine trade-offs.
4. Write Analysis inline using Folt's order: IMPORTANT decision callout; Where we are; Out of scope; Terms; comparison table if multiple options; Option blocks with You get, Changes, Watch out, Codex scope; Recommendation.
5. Claude explicitly answers the Question or selects the solution in a concise decision comment. Identify it as an autonomous Claude decision under this brief, cite the chosen option and artifact, and explain why it fits the constraints. If none fits, revise the scopes before selecting.
6. Mark the chosen attachment `[Selected Solution]`; fill Solution with changes, affected areas, and why this one.
7. Derive complete bounded children and prerequisites. Update the stack/decision record only after selection.

Treat missing vocabulary as unresolved until a Question defines it. Use `GLOSSARY.md` terms consistently.

Every scope includes standing rules, exact branch/worktree, allowed files, source-of-truth and vocabulary changes when relevant, file-by-file intent, boundaries, verification commands with expected results, required final response, and separate delivery/review notes for Claude.

Upload Markdown with the verified Folt `gh-attach` procedure. Verify upload contents by downloading and comparing. Do not replace attachments with gists, local paths, or unrelated repository commits. If uploads require unavailable browser authentication, record the actual blocker; do not mark the ticket ready prematurely.

## Plan the complete product before broad implementation

Use `plan.md` as a proposal, not evidence that capabilities were verified. Turn unresolved architecture into Questions and the accepted release scope into a complete, dependency-aware backlog.

Cover at least:

- Supported authentication, billing modes, and provider capability contracts.
- Agent lifecycle, streaming, permissions, cancellation, and session recovery.
- CLI commands and configuration, with a human approval default for product users.
- Typed workflow states, guards, roles, and structured handoffs.
- SQLite state, artifacts, operation records, restart reconciliation, and approvals tied to commit hashes.
- GitHub issue/project/relationship operations and artifact publication.
- Git worktrees, staging validation, commits, PRs, stacked changes, and CI collection.
- Token/context efficiency, bounded failure excerpts, usage reporting, and repair limits.
- Meaningful tests, packaging, CI, developer documentation, and user documentation.

Do not create a monolithic implementation ticket. Deliver a usable S-ticket workflow first, then analysis/selection and child tickets, recovery, concurrency, and stacks. Defer richer TUI work unless a decision demonstrates a release need.

## Provider feasibility before integration implementation

Read current official OpenAI and Anthropic documentation. Verify installation and runtime behavior using existing authorized accounts. Never treat the discussion's SDK/authentication assumptions as proven facts.

Prefer officially supported existing-subscription usage. Do not extract credentials, copy OAuth tokens between applications, implement unofficial endpoints, bypass provider restrictions, purchase credits, or silently fall back to API billing. If the desired route is unavailable, Claude selects a truthful supported alternative within already authorized billing limits, or records the specific account/permission blocker.

Keep authentication mode visible. Preserve provider capability differences in adapters. Do not equate a valid output schema with correct reasoning or a resumed session with a complete recovered workflow.

## Implementation and delivery loop

For each ready ticket, in dependency order:

1. Re-read the live issue and selected scope. Validate ownership, metadata, dependencies, artifact integrity, and readiness.
2. Fetch and fast-forward the base. Create one dedicated ticket branch and worktree. Independent work bases on develop; a stacked change bases on the lower branch. Use `<type>/<N>-<slug>`.
3. Dispatch Codex with the exact scope and implementation-only boundaries. Scope all edits and run it with the least required supported permissions.
4. Inspect the returned diff. Confirm boundaries and relevant tests. Diagnose failures and send precise fixes back to the same implementation session where useful.
5. Run the agreed Fugue gate in the worktree. Establish real formatting, lint, type, test, and packaging checks appropriate to the selected stack; record their commands in the process docs. Never push a failing gate or claim an unrun check passed.
6. Stage explicit intended files and inspect the staged diff. Keep commits focused. Use `<Issue Type>[#N]: <imperative explanation>` with the exact issue type, approximately 72 characters or fewer. Each body has `**Summary**`; add `**Changes**` where useful, explaining what changed and why. No attribution lines.
7. Push and create/update the PR without another user request. Give it assignee, milestone, and Fugue Project membership. Use the commit-subject format for the title.
8. PR sections: Summary, Changes, Related work, Verification, Risks and follow-up. Use `Closes #N` only when the PR completes the issue and parent closure rules allow it. State stack position and lower PR where relevant.
9. Wait for CI using ordinary polling. Collect failure excerpts and full logs. Diagnose substantive failures; repair, rerun the gate, and push new commits. Retry an unexplained infrastructure failure once; repeated failure becomes a recorded blocker.
10. Where size requires review, run independent read-only Codex review after CI finishes. Include actual CI results, the diff base/head, scope, and rejected findings with reasons. Post a COMMENT review with concrete inline findings where possible. Review agents cannot approve their own account's PR.
11. Fix accepted findings on the same branch. Explain rejected findings on their threads. Resolve fixed threads and repeat until an applicable review has no findings. File separate Incidents for unrelated existing defects outside the diff.
12. Claude checks the current head, gate, CI, review, and unresolved threads; records explicit merge authorization for that SHA; and merges with an expected-head check supported by the chosen GitHub operation. If head changes, revalidate before authorizing again. Never weaken branch rules to merge.
13. Verify the merge succeeded before cleanup. Set Disposition: Merged and Done for delivered work. Reconcile automatic issue closure and update durable records. Refresh dependent branches carefully and rerun affected verification.

Do not rewrite pushed commits except documented unmerged stack rebases. Use force-with-lease only for those and only after all affected branches pass the required gate. Stop on divergent stack state rather than overwrite other work.

For Dependabot, use `Dependabot[#0]: ...`, run the required read-only review using release notes and CI evidence, and post exactly `No issues.` when clean. No ticket is needed solely for that dependency bump.

## Mechanical operations and usage efficiency

Perform ticket fetching, metadata validation, worktree creation, check execution, CI polling, Git mutations, template rendering, and GitHub publication through ordinary code or commands. Do not spend an agent turn choosing a known next mechanical step.

Curated tools are appropriate when an agent needs to choose what evidence to retrieve. Return bounded structured data and artifact references. Keep complete logs locally, and let agents request more rather than flooding every session.

Use structured input/output, separate role sessions, and explicit handoffs. Avoid transferring whole conversations. Measure actual usage and elapsed time; do not invent dollar savings or assume subscriptions provide unlimited concurrency.

## Recovery, limits, and reporting

Before creating issues, branches, PRs, or comments after interruption, reconcile live state and reuse existing objects. Preserve useful work and unrelated changes. Use configured limits for repair attempts and concurrency, and prevent two writers sharing a ticket worktree.

Persist a campaign checkpoint with the active release, ticket, selected scope, worktree, branch, head, agent session identifiers, checks, reviews, pending actions, and blockers. Store no secrets in tickets, logs, repository artifacts, or checkpoints.

Continue independent ready work when one dependency is blocked. Do not loop indefinitely on authentication, usage exhaustion, unavailable attachment uploads, or repeated identical failures. Report the exact external action required when no independent work remains.

Keep user updates concise and factual. Do not ask the user to answer ordinary design Questions delegated to Claude. Do not claim success when a provider run, check, review, merge, or publication was not verified.

## Closure and release completion

Set Disposition before closing. Use Answered for Questions, Released for shipped release Epics, and the matching supported outcome for abandoned, duplicate, invalid, obsolete, superseded, withdrawn, out-of-scope, or rejected work. Record reasons for non-delivery closures.

Parents remain open until all children close. A Question may be Done/Answered while implementation children remain open. Superseded children retain their parent and close as Superseded.

Promote develop to main through a verified release PR. Do not publish to a package registry or incur external charges without separate authorization; prepare release artifacts and installation instructions locally or in the repository. If distribution is pending, record it accurately and do not call the release shipped prematurely.

Completion means the agreed release scope has working implementation, real verification, clean required reviews, merged changes, accurate issue/project state, documented limitations, and reproducible installation/use instructions. Provide links to the release Epic, key decisions, delivered PRs, verification evidence, and any specifically blocked remaining work.
