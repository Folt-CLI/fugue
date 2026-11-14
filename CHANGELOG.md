# Changelog

## 0.2.0

### Overview

Fugue 0.2.0 makes working with Fugue more comfortable and safer: tickets can be created and questions answered through guided conversations, runs can be started in parallel, roles can fall back between providers and choose models by ticket size, and a new overview shows where a repository stands.

### Included in this release

#### Features

- [#89] : `fugue new` turns an idea into a valid ticket through a guided conversation, with a live preview and explicit confirmation before anything is created.
- [#90] : Question tickets can be answered through a guided interview, including an automatic mode with a stand-in.
- [#91] : Each role takes an ordered list of providers and continues with the next one when the first is limited or unavailable.
- [#92] : Model tiers chosen by ticket size, with per-run and per-role overrides.
- [#93] : `fugue run` accepts several tickets, protects each ticket and the shared checkout with locks, and waits for the run database.
- [#94] : Status, usage and timing listings show the repository and can be filtered by it.
- [#95] : A shared terminal layer with live progress, plain-text fallback and a reusable chat component.
- [#96] : `fugue overview` reports open work, what is ready, blocked or waiting for a decision, open pull requests and unfinished runs.
- [#116] : Stacked pull requests created through the native stack extension.

#### Bugs Fixed

- [#98] : Inline code in review findings keeps its backticks.
- [#100] : A reviewer answer that starts with `No findings.` is treated as clean.
- [#101] : The reviewer receives the pull request diff in its prompt.
- [#104] : A solution can be selected from the command line for analysed M and L tickets.
- [#106] : Generated implementation scopes list the allowed files.


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
