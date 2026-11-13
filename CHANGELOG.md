# Changelog

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
