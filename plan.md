# Fugue

A local-first CLI that coordinates coding agents through a structured ticket-to-delivery workflow. Like independent voices in a fugue, agents contribute distinct work within a shared structure.

## Status and scope

This document captures the proposed design, not a claim that integrations have been tested. `prompt.md` is the separately requested execution brief for an autonomous Claude/Codex development campaign. During an explicitly invoked campaign, Claude resolves architecture Questions and Codex implements the selected scopes. Creating that brief and setting up GitHub do not themselves start application implementation.

The name has been selected for this local workspace; package, domain, and trademark availability have not been checked.

## Objective

Automate the existing ticket process while reducing unnecessary model turns and context. Ordinary application code handles predictable operations. Agents perform investigation, solution design, implementation, diagnosis, and review.

Primary providers are OpenAI Codex and Claude Code. Prefer existing subscriptions and official authentication capabilities where supported. Never silently fall back to usage-based API billing.

## Design principles

- Local-first, modular, and minimal dependencies.
- Encode workflow rules in code rather than repeatedly asking agents to remember them.
- Execute known operations directly; expose curated tools only where an agent must choose an action or information source.
- Keep provider sessions independent and transfer explicit artifacts between roles.
- Preserve meaningful differences in provider capabilities.
- Persist progress and reconcile external state before retrying side effects.
- Keep human solution selection and merge approval explicit.
- Support repository-specific policies instead of assuming every repository follows identical rules.

## Proposed technology stack

| Area | Initial choice | Rationale |
| --- | --- | --- |
| Language | Python | Local automation, typed data, subprocesses, and provider integrations |
| CLI | Typer and Rich | Commands, streaming output, progress, and approval prompts |
| Execution engine | Custom state machine | Explicit stages and inspectable transition rules |
| Concurrency | asyncio | Coordinate agents, subprocesses, and CI polling |
| Validation | Pydantic | Validate configuration, snapshots, handoffs, and results |
| Persistence | SQLite | Local runs, checkpoints, approvals, and operation records |
| Artifacts | Filesystem | Findings, scopes, diffs, logs, and structured outputs |
| Configuration | TOML | Repository policy and role/provider configuration |
| GitHub | gh with structured JSON output | Reuse supported GitHub authentication and existing operations |
| Git | Subprocesses with explicit argument lists | Worktrees, staging, commits, and branch management |
| Agent integration | Separate provider adapters | Normalize orchestration while retaining provider-specific behavior |

Defer Textual until an interactive dashboard is justified. Defer LangGraph until branching and checkpoint complexity warrant it. Do not introduce PydanticAI merely to wrap runtimes that already own their agent loops.

Python is the preferred initial choice. TypeScript remains a viable alternative if capability testing reveals materially better integration support. Framework and dependency versions must be selected and pinned during implementation planning.

## Provider feasibility and authentication

Verify these paths before committing to the architecture. Documentation findings below are a research baseline from the design discussion, not account-level verification.

### Codex

Official documentation describes a Python SDK, `openai-codex`, controlling the local app-server, including async usage and a pinned runtime. Codex supports ChatGPT subscription sign-in and cached authentication. Authentication guidance also recommends API keys for programmatic CLI workflows such as CI/CD.

Validate the intended personal, local workflow against supported SDK authentication behavior and applicable subscription rules. Determine how runtime selection affects reuse of existing CLI authentication. Do not extract or copy credentials as an integration mechanism.

Sources:

- https://learn.chatgpt.com/docs/codex-sdk
- https://learn.chatgpt.com/docs/auth
- https://learn.chatgpt.com/docs/app-server

### Claude

The Claude Agent SDK provides Python and TypeScript interfaces. Its documentation directs third-party applications toward API-key authentication and restricts offering Claude subscription login without approval. Anthropic separately documents end-user subscription sign-in to the unmodified Claude Code binary under stated conditions.

Treat SDK integration and subscription-backed CLI execution as distinct choices. Validate whether this local orchestrator's intended use is supported; launching a subprocess is not automatically an exception to authentication restrictions. Never collect or intermediate Claude session tokens.

Eligible subscription plans may provide claimable API credits covering SDK usage. Such usage is still metered API usage, with a finite credit balance, rather than ordinary Claude Code subscription allowance.

Sources:

- https://code.claude.com/docs/en/agent-sdk/overview
- https://code.claude.com/docs/en/headless
- https://code.claude.com/docs/en/legal-and-compliance
- https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers

### Required adapter capability checks

- Authentication mode, account eligibility, and billing behavior.
- Streaming events and validated final results.
- Session creation, continuation, and recovery after process restart.
- Cancellation, timeouts, and provider-limit handling.
- Read-only and workspace-write controls, tool permissions, and approval handling.
- Structured outputs and tool integration where supported.
- Usage reporting, including fields unavailable for subscription-backed sessions.

Adapters expose capabilities explicitly. Unsupported capabilities must produce a clear result rather than be silently emulated.

## Architecture

The CLI calls a workflow engine. The engine evaluates repository policy, persists execution state, invokes direct operations, and dispatches bounded reasoning tasks through provider adapters.

Logical modules:

- CLI: commands, output, selections, and approval interactions.
- Workflow: stage execution, transitions, readiness guards, retries, and cancellation.
- Policy: size rules, required fields, checks, permitted actions, and closure rules.
- Integrations: GitHub, Git, check runners, and artifact publication.
- Agents: Codex and Claude adapters, capabilities, sessions, and event normalization.
- Context: role-specific input assembly and artifact selection.
- Persistence: runs, steps, attempts, external identifiers, and approvals.
- Artifacts: immutable snapshots, scopes, findings, diffs, and logs.

No model controls workflow transitions directly. An agent's success message is input to validation, not proof that a stage is complete.

## Workflow mapping

| Stage | Direct application operations | Reasoning or human contribution |
| --- | --- | --- |
| Load | Fetch issue, relationships, metadata, comments, attachments | Interpret ambiguity when needed |
| Validate | Apply configured type, size, field, and readiness rules | Recommend resizing or splitting |
| Analyze | Prepare context and store/publish artifacts | Agent investigates and proposes solutions |
| Select | Present options and record selected scope | Developer chooses |
| Prepare | Refresh base; create branch and isolated worktree | None normally |
| Implement | Launch agent in assigned workspace | Agent changes code and relevant tests |
| Verify | Run configured checks and capture results | Agent diagnoses substantive failures and fixes them |
| Deliver | Validate staging; commit; push; create/update PR and metadata | Agent drafts rationale and explanation |
| Monitor | Poll CI and fetch bounded failure excerpts | Agent diagnoses substantive CI failures |
| Review | Assemble evidence and publish structured review | Separate agent reports actionable findings |
| Repair | Route accepted findings to implementation session | Agent fixes; developer/orchestrator handles disputed findings |
| Merge | Validate checks and current-head approval | Developer merges or explicitly authorizes merge |
| Close | Set Disposition and validate child closure | Human explains exceptional outcomes where needed |

Publishing findings and scope attachments requires a verified, supported upload mechanism. Existing browser-dependent attachment steps may remain a manual checkpoint until an appropriate integration is established. Do not substitute an unverified upload path.

## Ticket policy baseline

Use the documented Folt process as the initial reference, with configurable overrides for other repositories.

Ordinary work items contain Summary, Scope, Analysis, Solution, and Details. Summary and Scope are required at creation. Delivery and verification belong in the PR. Epics and Incidents have their documented special formats.

| Size | Analysis | Review | Component rule |
| --- | --- | --- | --- |
| XS | No | No | Exactly one |
| S | No | Yes | Exactly one |
| M | Yes | Yes | Up to two |
| L | Yes | Yes | Question with bounded children |
| XL | No | No | Release Epic; limit unresolved |

S/XS require a solution at creation. S requires a complete scope attachment; self-descriptive XS may omit it. M/L require developer selection before implementation readiness.

Required planning data includes owner, project, milestone, priority, size, status, and native parent. Native blocked-by relationships represent actual prerequisites. Component and Disposition follow the repository's rules.

Ticket statuses are S0: To Do, S1: In Analysis, S3: Ready For Implementation, S4: In Implementation, and S5: Done. The documented suggested paths and unresolved XL rules must not be mistaken for fully agreed policy.

Keep internal execution states separate: checking, repairing, waiting for CI, waiting for selection, waiting for merge approval, interrupted, and provider-limited do not require new GitHub statuses.

Each implementation ticket uses its own branch and dedicated worktree. Independent work starts from develop; dependent stacks use the lower branch. Parent closure waits for all children. Set Disposition before closure, using Merged only for work delivered by a merged PR.

Sources in the current workspace:

- /home/admin/Coding/foltivo/folt/docs/dev/process.md
- /home/admin/Coding/foltivo/folt/docs/dev/working/README.md
- /home/admin/Coding/foltivo/folt/docs/dev/issue-format.md
- /home/admin/Coding/foltivo/folt/docs/dev/ticket-planning.md
- /home/admin/Coding/foltivo/folt/docs/dev/workflow.md
- /home/admin/Coding/foltivo/folt/docs/dev/testing.md
- /home/admin/Coding/foltivo/folt/docs/dev/review.md

## Deterministic operations and curated tools

Known workflow actions call application functions directly. Fetching a ticket or updating a status must not require a model turn. Commit execution belongs to the delivery stage, with validated paths, check results, and branch state.

Expose narrow tools for information an agent chooses to request, such as reading an attachment, searching code, or paging through a check failure. Reuse the same underlying services for engine calls and agent tools. Avoid a large general-purpose tool catalog.

Tool results include concise structured data and references to complete evidence. Tool calls still consume model context; bypassing an unnecessary agent turn generally saves more than renaming an operation as a tool.

Run subprocesses using explicit arguments, controlled working directories, timeouts, and cancellation. Validate side effects outside agent prompts. Worktree isolation alone is not a security boundary; enforce role permissions with supported runtime and OS controls.

## Agent roles and handoffs

Roles are analyst, implementer, reviewer, and diagnostic reasoning where needed. Provider/model selection is configurable per role rather than hardcoded to a vendor.

The engine replaces the mechanical portion of the current orchestrator role. Semantic investigation, interpretation of failures, and review adjudication still require an agent or human.

Use separate review sessions. Resume implementation sessions for repair when their context is useful. Do not attempt to translate private provider session state between vendors.

Handoffs contain the applicable ticket sections, selected scope, relevant repository instructions, artifacts, base/head identifiers, and an expected result schema. Findings include severity, location, concrete trigger, explanation, and suggested correction where clear.

Validate output schemas, but do not treat schema compliance as evidence of factual correctness.

## Persistence and recovery

Store runs, stage attempts, agent session identifiers, artifact references, operation intents/results, external identifiers, approval records, and configuration versions in SQLite. Store larger content in files with checksums.

GitHub remains authoritative for shared ticket/PR state. SQLite tracks execution and cached snapshots. Refresh external state at meaningful boundaries and detect conflicting edits before overwriting them.

Record operation intent before external mutations and reconcile afterward. Following a crash, discover an existing PR or commit before retrying creation. Do not promise exactly-once behavior across SQLite, Git, and GitHub.

Bind merge approval to the reviewed PR head commit. New commits invalidate approval. Bind verification and review evidence to the relevant code and scope versions.

Use bounded retries for transient failures. Authentication failures, usage limits, and missing human decisions pause the run with a specific next action. Repeated repair failures stop for intervention.

## Token and usage efficiency

- Cache issue data per stage and refresh at decision/publication boundaries.
- Pass only role-relevant context; retain full source evidence locally.
- Execute checks and CI polling without an agent.
- Send bounded failure excerpts with access to full logs.
- Render fixed ticket and PR structures in application code.
- Transfer concise structured results instead of whole conversations.
- Preserve implementation context for useful repair loops.
- Limit concurrency, repair attempts, and agent turns.
- Record available provider usage data without inventing dollar costs for subscriptions.

Measure usage and latency per stage before claiming savings. Reduced context can backfire if agents must rediscover omitted information. Subscription efficiency preserves allowance and responsiveness; it does not automatically reduce the monthly bill.

## Concurrency and approvals

Permit one implementation writer per ticket worktree. Parallelize independent tickets only within configured capacity. Coordinate shared repository mutations and dependencies explicitly.

Developer selection gates M/L implementation. Merge requires developer action or approval tied to the current head. Other approvals follow configured repository policy and previously authorized scope, without prompting repeatedly for routine operations.

## Proposed delivery sequence

1. Resolve provider authentication eligibility and demonstrate supported session, streaming, cancellation, permissions, and usage behavior in an explicitly authorized feasibility phase.
2. Agree the state model, provider interface, repository policy format, and artifact contracts.
3. Build a minimal vertical slice for an already-ready S ticket: load, validate, worktree, implement, verify, PR, review, and wait for merge approval.
4. Add M/L analysis, scope publication, solution selection, and child-ticket handling.
5. Add restart recovery, external-state reconciliation, bounded repair loops, and usage reporting.
6. Add multiple-ticket concurrency and stacked PR support once the serial workflow is reliable.
7. Add a richer TUI only if actual usage demonstrates the need.

Each implementation phase needs meaningful verification of transitions, interrupted side effects, stale approvals, permission boundaries, and adapter contracts.

## Open decisions

- Personal-only tool or intended distribution to other users?
- Exact subscription plans and supported integration mode for each provider?
- Whether Claude's supported SDK/API path or unmodified CLI path meets the requirements?
- Which roles use which providers/models, and what fallback is explicitly permitted?
- How GitHub Markdown attachment publication will work reliably?
- Which repository supplies the first executable policy?
- Maximum concurrency, repair attempts, and usage limits?
- How disputed review findings are resolved and recorded?
- Whether XL status transitions and Component limits should be defined before supporting release orchestration?

This planning document alone does not start implementation. See `prompt.md` for the campaign's execution instructions and `github-setup.md` for actual setup status and remaining configuration gaps.
