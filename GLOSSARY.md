# Fugue glossary

Read this file first. Use defined terms exactly as written. Product meanings
remain unresolved until a Question defines them; do not infer them from proposals.

## A-Z index

- [adapter](#provider-adapter)
- [approval](#approval)
- [artifact](#artifact)
- [authentication mode](#authentication-mode)
- [autonomy exceptions](#autonomy-exceptions)
- [batch](#batch)
- [blocked ticket](#blocked-ticket)
- [blocking finding](#blocking-finding)
- [campaign](#campaign)
- [capability](#capability)
- [chat component](#chat-component)
- [child](#child)
- [Component](#component)
- [conversation attachment](#conversation-attachment)
- [cool-down](#cool-down)
- [Disposition](#disposition)
- [Epic](#epic)
- [gate](#gate)
- [handoff](#handoff)
- [incident](#incident)
- [issue type](#issue-type)
- [milestone](#milestone)
- [model override](#model-override)
- [model tier](#model-tier)
- [non-blocking finding](#non-blocking-finding)
- [operation](#operation)
- [orchestrator](#orchestrator)
- [parent](#parent)
- [provider adapter](#provider-adapter)
- [provider fallback](#provider-fallback)
- [Question](#question)
- [ready to start](#ready-to-start)
- [reconcile](#reconcile)
- [repair limit](#repair-limit)
- [repository overview](#repository-overview)
- [repository lock](#repository-lock)
- [role](#role)
- [run](#run)
- [scenario](#scenario)
- [scope](#scope)
- [session](#session)
- [Size](#size)
- [stack](#stack)
- [stage](#stage)
- [Status](#status)
- [terminal layer](#terminal-layer)
- [ticket draft](#ticket-draft)
- [ticket lock](#ticket-lock)
- [transcript](#transcript)
- [unavailable provider](#unavailable-provider)
- [unfinished run](#unfinished-run)
- [usage record](#usage-record)
- [waiting for a decision](#waiting-for-a-decision)
- [work item](#work-item)
- [worktree](#worktree)

## Process terms

### autonomy exceptions

Campaign-only overrides of ordinary developer decision and merge rules, defined in the development process. They do not change the product defaults.

Status: decided.

### blocking finding

A review finding that blocks a merge: a defect in code changed by the diff, with a concrete trigger reachable in an in-scope scenario or in ordinary use of the feature, a wrong result that matters (data loss, security hole, incorrect output or a crash in normal operation) and reproduction steps with inputs. The review contract defines the criteria.

Status: decided.

### campaign

Development of Fugue under prompt.md, with the explicit autonomy exceptions recorded in the development process.

Status: decided.

### child

An issue grouped under a parent through a native relationship. A parent cannot close until all children close; prerequisites use separate dependency links.

Status: decided.

### Component

The issue field describing affected areas after solution selection: Build, Dev Docs, Dev Tools, Source Code, Tests or User Docs. XS/S have exactly one, M up to two, L several represented by children, XL none.

Status: decided.

### Disposition

The issue field recording the outcome before closure: Abandoned, Answered, Duplicate, Invalid, Merged, Obsolete, Out of Scope, Released, Superseded, Withdrawn or Won't Do. It is distinct from Status.

Status: decided.

### Epic

A release grouping issue of Size XL. Its title is <version> -- Fugue and its body contains only Objective. It has no Component and closes with Released after all children close.

Status: decided.

### gate

The required local verification the orchestrator runs in the worktree before a push, with every command passing. CI separately requires the full required check. The Testing guide defines the commands.

Status: decided.

### incident

A report of an observed problem and its impact, with evidence and separate linked response work. The report is not the fix.

Status: decided.

### issue type

The native kind of work: Task, Bug, Feature, Question, Epic, Maintenance, Documentation, Incident or Test. Use the exact type in commit subjects.

Status: decided.

### milestone

The required release assignment on each ticket and PR: 0.1.0 for this release or Backlog for unscheduled work.

Status: decided.

### non-blocking finding

A review finding that never blocks a merge: hardening, rare or pathological timings, speculative misuse or style. A finding about an out-of-scope scenario is non-blocking by definition.

Status: decided.

### orchestrator

The role that investigates, publishes decisions and scopes, runs the gate, interprets CI, and delivers changes. Implementation and independent read-only review remain separate roles.

Status: decided.

### parent

An issue grouping children through native relationships. Derived work belongs under its Question; otherwise use an existing delivery group or release Epic #1 unless instructed otherwise. Grouping does not order dependencies.

Status: decided.

### Question

A decision issue whose answer is explicitly selected and recorded. L is always a Question and splits into M/S/XS children. Ordinary use requires developer selection; the campaign exceptions assign that role to Claude.

Status: decided.

### scenario

A numbered situation with an expected outcome that a scope lists as in scope or out of scope.

Status: decided.

### scope

The explicit boundaries of a ticket or selected implementation attachment, including allowed files, intended changes, exclusions and verification.

Status: decided.

### Size

The scope category XS, S, M, L or XL. XS needs neither analysis nor review; S has a decided solution and needs review; M needs analysis and review; L is a Question split into children; XL is a release Epic.

Status: decided.

### stack

A chain of pull requests in one repository, with the bottom targeting develop and each upper PR targeting the branch below. Each layer has its own ticket and worktree.

Status: decided.

### Status

The planning field with S0: To Do, S1: In Analysis, S3: Ready For Implementation, S4: In Implementation and S5: Done. XS/S/XL use S0 -> S4 -> S5; M/L use S0 -> S1 -> S3 -> S4 -> S5. XL enters S4 when its first child does and never uses S1 or S3.

Status: decided.

### work item

A bounded ticket using Summary, Scope, Analysis, Solution and Details, distinct from release Epic Objective bodies and Incident reports.

Status: decided.

### worktree

A dedicated Git working directory for an issue branch. Concurrent implementation tickets must not share one worktree; the primary checkout is not an implementation workspace.

Status: decided.

## Product terms

### approval

a recorded decision bound to the exact head commit SHA of a pull request; invalid when the head differs.

Status: decided.

### artifact

a file produced or consumed by a run (findings, scope, diff, log, structured output), stored with a SHA-256 checksum.

Status: decided.

### authentication mode

how a provider call is authenticated and billed; one of `subscription_login` (the user's own sign-in to the unmodified provider CLI) or `api_key` (explicitly configured, metered).

Status: decided.

### batch

one `fugue run` invocation naming several tickets.

Status: decided.

### blocked ticket

an open ticket with at least one open native blocker; distinct from the run state blocked.

Status: decided.

### capability

a feature a provider adapter declares (streaming_events, structured_output, session_resume, cancellation, read_only_mode, workspace_write_mode, usage_tokens, usage_cost_estimate).

Status: decided.

### chat component

the reusable conversation loop with streaming output, an input line, slash commands (`/help`, `/save PATH`, `/quit`) and a saved transcript; the Question walkthrough's follow-up uses it.

Status: decided.

### conversation attachment

the `<N>-conversation.md` file uploaded to a ticket created with `fugue new`: the verbatim turns of the creation conversation, the final preview and the decision, with strings matching known token patterns redacted.

Status: decided.

### cool-down

the interval after an unavailable result during which a later call tries other providers first.

Status: unresolved.

### handoff

the schema-validated structured input or output passed between roles at a stage boundary.

Status: decided.

### model override

a per-run tier or explicit model identifier on the command line that outranks configuration.

Status: decided.

### model tier

one of small, medium or large; configuration maps it per provider to a model identifier.

Status: decided.

### operation

one external mutation (GitHub, Git, filesystem outside the run store) recorded with an intent before and a result after, so a restart can reconcile.

Status: decided.

### provider adapter

the module that drives one provider's unmodified command-line tool as a child process and normalises its events, sessions and usage.

Also called adapter.

Status: decided.

### provider fallback

an ordered list of providers for one role, tried in order, whole calls only.

Status: unresolved.

### ready to start

an open ticket with no readiness problems, Status S0 (XS/S) or S3 (M/L), not a Question or XL, and no open blockers.

Status: decided.

### reconcile

compare recorded operation intents with the live external state after an interruption and reuse or complete existing objects instead of repeating them.

Status: decided.

### repair limit

the configured maximum number of repair attempts for one stage before a run stops for intervention.

Status: decided.

### repository lock

the cross-process lock held around the shared Git steps in the primary checkout.

Status: decided.

### repository overview

the single report of open tickets, pull requests and unfinished runs for the current repository, gathered without an agent.

Status: decided.

### role

a named job for an agent in a run: analyst, implementer or reviewer.

Status: decided.

### run

one execution of the workflow for one ticket, recorded in the run store with an id.

Status: decided.

### session

the provider-side conversation identifier a provider adapter retains so work can be resumed.

Status: decided.

### stage

a named step of a run (load, validate, analyse, select, prepare, implement, verify, deliver, monitor, review, repair, merge, close).

Status: decided.

### terminal layer

the one module every command uses for styled status, tables, panels, Markdown and progress; it degrades to plain text when output is piped, captured, or `NO_COLOR` or `TERM=dumb` is set.

Status: decided.

### ticket draft

the saved state of one `fugue new` conversation at `<state_dir>/drafts/<id>.json`: the ticket fields, who owns each field (person or analyst), the turns and the creation journal. It is not a ticket and holds no environment values or tokens.

Status: decided.

### ticket lock

the cross-process lock held for the whole run of one ticket.

Status: decided.

### transcript

the UTF-8 text file a chat session saves on request with `/save PATH`; it never overwrites an existing file.

Status: decided.

### unavailable provider

a provider whose call ended rate or usage limited, unreachable or not signed in, reported as a structured reason.

Status: unresolved.

### unfinished run

a run whose state is not completed.

Status: decided.

### usage record

tokens, elapsed time and, where the provider reports it, a cost estimate for one agent call; never presented as a bill.

Status: decided.

### waiting for a decision

an M/L ticket or Question with Analysis and no [Selected Solution], or a run in waiting_for_selection, waiting_for_decision or waiting_for_merge_approval.

Status: decided.
