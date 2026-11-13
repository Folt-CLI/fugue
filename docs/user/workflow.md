# Workflow

A run executes the workflow for one ticket and retains progress in the local run
store. A role is an agent job: analyst, implementer or reviewer. A stage is a
named step such as analyse, implement, verify or review. See the
[glossary](../../GLOSSARY.md) for the precise terms.

Examples below assume you are in the target repository and Fugue is available
through `uv run`. When using a separate Fugue source checkout, append
`--repo-root /path/to/repository` to each command. Use `--config` consistently
when the configuration is outside the repository's `fugue.toml`.
Ticket 47 and run 1 are examples; replace them with actual ids.

## Start and inspect

```sh
uv run fugue run 47
uv run fugue run 47 --to-green
uv run fugue status
uv run fugue status --ticket 47
uv run fugue status 1
```

`run` drives a ticket to a pull request; `--to-green` also waits for green CI.
These are alternative starting commands. They can invoke providers and mutate
Git, the filesystem and GitHub. The workflow validates the ticket, uses a
worktree, implements the selected scope, verifies changes and delivers a pull
request. Size M and L tickets needing analysis stop for solution selection.
A Size XL Epic groups release work and is not an implementation ticket.

`status` lists up to 20 recent runs, optionally filtered by ticket. A run detail
shows stage attempts, operations, approvals and usage records. Follow the printed
state, problems and next action; a successful command can still be waiting for
a human decision.

## Overview

```sh
uv run fugue overview
uv run fugue overview --no-summary
uv run fugue overview --refresh
```

`overview` prints the repository overview: one report of the current
repository's open tickets, pull requests and unfinished runs. It changes
nothing: no ticket, Status, comment, pull request or run is created or modified,
and a missing run store is not created. It needs `github.project_owner` and
`github.project_number`; outside a Git repository, without an `origin` remote or
without those settings it prints an `error:` line and exits with 2 before any
GitHub or agent call.

The report holds, in order: a header, `note:` lines, an optional summary, open
tickets grouped by Status (ticket, type, size, priority, title), Epic and
Question progress as `closed/total` children, tickets ready to start with
`fugue run N`, blocked tickets with `blocked by #A, #B` (closed blockers are not
counted), items waiting for a decision with their command, open pull requests,
unfinished runs, the five most recently merged pull requests and the five most
recently closed tickets. An empty section prints `<title>: none`.

- A ticket is ready to start when it passes the readiness checks of `run`, has
  Status S0 (XS/S) or S3 (M/L), is not a Question, Epic or XL and has no open
  blocker.
- A ticket is waiting for a decision when it is an M/L ticket or Question with
  an Analysis and no `[Selected Solution]` marker (`fugue question N`), or when
  a local run is in `waiting_for_selection`, `waiting_for_decision` or
  `waiting_for_merge_approval`, shown with the exact command that continues it.
- A pull request shows its CI check counts as `pass/fail/pending`, its GitHub
  review decision and `approved yes` only when a valid merge approval for that
  pull request matches its current head SHA (shown as the first 7 characters).
  After a new head the same pull request shows `approved no`.
- Unfinished runs are the runs of this repository that are not completed,
  newest first, at most 20, with the time since their last activity and the
  `next:` command.

At most `limits.overview_max_tickets` open tickets are read; if more exist the
report says `note: showing N of more than N open tickets`. A ticket that cannot
be read (for example absent from the project) is listed as `note: could not
read #N: reason` and the rest still prints. When GitHub cannot be reached the
report prints `note: GitHub unreachable (...); showing local runs only`, still
lists the unfinished runs, skips the summary and exits with 0.

Unless `--no-summary` is given, the analyst role writes a short summary of the
gathered facts. The request is read-only, runs in an empty temporary directory
rather than the repository, uses the small model tier when `model_tiers` is
configured and sees only a bounded text of the facts. A reply that cites a
`#N` not in the facts is dropped. The summary is skipped, with the reason in a
`note:` line and the rest of the report unchanged, when the adapter cannot be
built or lacks read-only mode, the call fails, times out or is limited, or the
reply is empty. A reported usage record is shown as reported values, not a bill.

The summary is cached in `overview-OWNER-NAME.json` in the state directory for
`limits.overview_cache_seconds`; it is reused only while the gathered facts are
identical. `--refresh` ignores the cache and asks again. The cache file is the
only file the command writes, and only when a summary was produced.

## Terminal output

On a terminal, output uses colour and a single run shows a live spinner with
the current stage. When output is piped or captured, or `TERM=dumb` is set, it is
plain text with one line per stage. Set `NO_COLOR` to turn colour off; meaning
is always in the text (`error:`, `warning:`, `next:`). `COLUMNS` sets the width;
tables wrap without cutting run ids or ticket numbers. A batch never uses the
spinner, and its lines keep their `[#N]` prefix.

The follow-up question in `fugue question` streams the answer as it arrives.
The chat component also understands `/help` (list commands), `/save PATH`
(write the transcript to a new UTF-8 file; an existing file is never
overwritten) and `/quit`. End a line with a backslash to continue it on the
next line. End of input also ends a chat.

## Select a solution

Browse a Question interactively, or explicitly select an option with a reason:

```sh
uv run fugue question 47
uv run fugue question 47 --select 1 --reason 'This scope meets the requirements' --by developer
```

The same command selects the solution for an analysed M or L ticket of any
issue type that has Analysis options, for example a Feature in
`waiting_for_selection`.

These are alternative ways to select. Check the proposed scope before recording
a choice. Human selection is the default; `resume` does not supply that decision.

`children` accepts a TOML child specification for a decided Question. Preview
before applying; even preview reads GitHub and requires its configuration:

```sh
uv run fugue children children.toml
uv run fugue children children.toml --apply
```

The second command creates children and their native relationships. Use a
prepared, validated child specification; the filename is an input file you
supply, not a file generated by these commands.

## Review and repair

```sh
uv run fugue review 1
```

Review evaluates the pull request head. A blocking finding blocks merge; a
non-blocking finding does not. When a decision is requested, use the finding
indices printed for that review. For example, if findings 1 and 2 exist:

```sh
uv run fugue decide 1 --accept 1 --reject '2:Outside the selected scope'
```

Accept findings to request repair, or reject them with a reason. Repeat
`--accept` or `--reject` for additional indices. Indices must be positive and
cannot be repeated in the same decision. Do not apply these sample decisions
without checking the actual findings. Repair is bounded by the configured
repair limit; changed code needs verification and review of its new head.

## Request approval and merge

After the gate, CI and required review are satisfied:

```sh
uv run fugue request-approval 1
```

Inspect the requested head SHA and the results. The developer can then record
approval and explicitly merge:

```sh
uv run fugue approve 1 --reason 'Gate, CI and required review are satisfied' --by developer
uv run fugue merge 1
```

`approve` requires a non-empty reason; `--by` defaults to the OS user. Approval
is bound to the exact head commit SHA. A new commit, including conflict
resolution, invalidates it and requires a new request and approval. The example
reason is a decision you must substantiate. `merge` merges the approved head and
closes the ticket subject to workflow checks; parents wait for closed children.

## Recover and measure

```sh
uv run fugue resume 1
uv run fugue usage --by role
uv run fugue usage --by provider --ticket 47
uv run fugue usage --by run
uv run fugue usage --by ticket
uv run fugue timing
uv run fugue timing 1
```

`resume` continues the same run from its last unfinished stage. Fugue can
reconcile recorded operation intents with external state after interruption,
reusing existing objects. Preserve the state directory and inspect the problem
before retrying. Resume does not grant solution selection or merge approval.

Usage records contain reported tokens, elapsed time and, where available, cost
estimates. `n/a` means the provider did not report the value. Estimates are never
a bill. `timing` reports stage attempt durations in seconds; an unfinished attempt
shows `n/a`. See [limitations](limitations.md) for exit codes and recovery advice.
