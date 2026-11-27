# Changelog

## Unreleased

### Included in this release

#### Features

- [#TBD] : S and XS tickets now get a full analysis, as an M does, and are selected by policy when no trigger fired; they are no longer scoped by default and still have no review. In a parent run, the ready S and XS children are analysed together in analysis batches of up to `limits.analysis_batch_size` (default 5), by Priority then ticket number, each with its own complete analysis, trigger check and selection; a child whose analysis is missing or invalid is analysed again alone, and a fired trigger parks only that child. M and L are analysed one agent per ticket. Nothing is analysed before its blockers are merged: a blocked child or M Question waits for them.

- [#TBD] : A parent run starts its ready children by Priority, most urgent first, then by ticket number; a child still waits for its open blockers whatever its Priority.

- [#TBD] : Sizes now set how deep analysis goes. An S ticket is scoped when its run starts instead of carrying a scope attachment, and is re-sized to M when it does not fit; S is no longer reviewed. M and L analyses check four triggers, and an M with none fired is selected by policy and goes on to implement. `policy.size_rules` takes `analysis = "none"`, `"scope"` or `"full"` and `selection = "developer"` or `"policy"`. Child tickets carry their outcome, scenarios and agreed constraints instead of a solution, and implementation scopes no longer carry standing rules or orchestrator notes for the agent. An Analysis publishes one scope, for the recommended option; selecting another option scopes it first. A Question whose Size selects by policy (M by default) answers itself in `fugue run` or `fugue question`: analysis, selection by policy, then its children are created without a prompt; a fired trigger stops it for you. Its children join the parent run's work on the same feature branch. An S run blocked outside its scoped files or at its repair limit is re-sized to M. `fugue report sizes` shows each run's original Size, re-size, selection, repairs and review rounds.

- [#TBD] : `fugue run` on a Question or Epic works the children on one feature branch: each child starts from its current head, merges into it with a merge commit once green and reviewed, and up to `limits.parallel_children` children run at once. One feature pull request then delivers the feature branch to `develop` after your approval of its exact head. `--authorise` covers the merges into the feature branch only. Stacked pull requests are no longer used, and a single ticket with an open blocker now stops until the blocker merges.

## 0.2.0

### Overview

Fugue 0.2.0 makes working with Fugue more comfortable and safer: tickets can be created and questions answered through guided conversations, runs can be started in parallel, roles can fall back between providers and choose models by ticket size, and a new overview shows where a repository stands. Pushes now stop with a clear reason when the remote branch has moved on, and repair attempts start fresh when the scope changes.

### Included in this release

#### Features

- [#53] : Branch pushes check whether the remote branch is ahead or diverged and stop with a clear reason, and repair attempts are counted per implementation.
- [#77] : `fugue reviewer-app setup` creates your own reviewer GitHub App through the manifest flow and stores its key privately.
- [#78] : `fugue protection` reports whether branch protection matches the review rules, with guidance for enabling them.
- [#89] : `fugue new` turns an idea into a valid ticket through a guided conversation, with a live preview and explicit confirmation before anything is created.
- [#90] : Question tickets can be answered through a guided interview, including an automatic mode with a stand-in.
- [#91] : Each role takes an ordered list of providers and continues with the next one when the first is limited or unavailable.
- [#92] : Model tiers chosen by ticket size, with per-run and per-role overrides.
- [#93] : `fugue run` accepts several tickets, protects each ticket and the shared checkout with locks, and waits for the run database.
- [#94] : Status, usage and timing listings show the repository and can be filtered by it.
- [#95] : A shared terminal layer with live progress, plain-text fallback and a reusable chat component.
- [#96] : `fugue overview` reports open work, what is ready, blocked or waiting for a decision, open pull requests and unfinished runs.
- [#116] : Stacked pull requests created through the native stack extension.
- [#124] : Reviews are posted as the reviewer app when `reviewer_identity_separate` is set, so approvals and change requests are accepted.

#### Bugs Fixed

- [#98] : Inline code in review findings keeps its backticks.
- [#100] : A reviewer answer that starts with `No findings.` is treated as clean.
- [#101] : The reviewer receives the pull request diff in its prompt.
- [#104] : A solution can be selected from the command line for analysed M and L tickets.
- [#106] : Generated implementation scopes list the allowed files.
- [#118] : Codex usage limits are recognised from the exact limit message and structured code instead of broad words.
- [#125] : Tickets with several Components can be loaded.


## 0.1.0

### Overview

Fugue 0.1.0 is the first release: a local-first command line tool that takes a ready ticket through implementation, verification, a pull request, independent review and an approval-bound merge, using the provider tools you already sign in to.

### Included in this release

#### Features

- [#33] : Ticket-to-pull-request engine that prepares an isolated worktree, runs the implementation agent, verifies with your gate and opens the pull request, resuming from live Git and GitHub state.
- [#36] : The `fugue` command with `run`, `status` and `config` for driving and inspecting runs.
- [#38] : CI monitoring that reruns infrastructure failures once and repairs real failures within configured limits.
- [#39] : Independent review published as GitHub code review with inline threads and suggestions, replies on every finding and resolved threads.
- [#40] : Merge approval bound to the exact head commit, verified merge and ticket closure.
- [#41] : `review`, `decide`, `request-approval`, `approve`, `merge` and `resume` commands for each decision point.
- [#42] : Interactive walkthrough for answering Question tickets.
- [#43] : Analysis stage that publishes findings and candidate solution scopes for medium and large tickets.
- [#44] : Child ticket creation from a decided Question with parents, fields and dependencies.
- [#45] : Usage and stage timing reports from recorded runs.
- [#46] : Bounded concurrent runs with per-ticket locks.
- [#27] : Codex adapter for the unmodified Codex CLI with your own sign-in.
- [#28] : Claude adapter for the unmodified Claude Code CLI with your own sign-in.
- [#12] : Local run store with recorded operations, approvals and usage.
- [#13] : Artifact store with SHA-256 checksums.
