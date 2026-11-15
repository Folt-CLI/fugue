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

## Command names in 0.3.0

Reporting, review, approval and setup commands use grouped names in 0.3.0.
Use the new invocations below; arguments and options follow the new command name.

| Old invocation | New invocation |
| --- | --- |
| `fugue status` | `fugue report runs` |
| `fugue usage` | `fugue report usage` |
| `fugue timing` | `fugue report timing` |
| `fugue overview` | `fugue report overview` |
| `fugue review` | `fugue review start` |
| `fugue decide` | `fugue review decide` |
| `fugue request-approval` | `fugue approval request` |
| `fugue approve` | `fugue approval record` |
| `fugue config show` | `fugue setup config` |
| `fugue protection` | `fugue setup protection` |
| `fugue reviewer-app setup` | `fugue setup reviewer-app` |

## Start and inspect

```sh
uv run fugue run 47
uv run fugue run 47 --to-green
uv run fugue report runs
uv run fugue report runs --ticket 47
uv run fugue report runs 1
```

`run` drives a ticket to a pull request; `--to-green` also waits for green CI.
These are alternative starting commands. They can invoke providers and mutate
Git, the filesystem and GitHub. The workflow validates the ticket, uses a
worktree, implements the selected scope, verifies changes and delivers a pull
request. Size M and L tickets needing analysis stop for solution selection.
A Size XL Epic groups release work and is not an implementation ticket.

`report runs` lists up to 20 recent runs, optionally filtered by ticket. A run detail
shows stage attempts, operations, approvals and usage records. Follow the printed
state, problems and next action; a successful command can still be waiting for
a human decision.

## Overview

```sh
uv run fugue report overview
uv run fugue report overview --no-summary
uv run fugue report overview --refresh
```

`report overview` prints the repository overview: one report of the current
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

## New ticket

```sh
uv run fugue new
uv run fugue new --suggest
uv run fugue new --resume ID
uv run fugue new --parent N --assignee LOGIN
```

`new` creates one ticket through a guided conversation. It needs
`github.project_owner` and `github.project_number`; outside a Git repository or
without those settings it prints an `error:` line and exits with 2 before any
GitHub or agent call. Nothing is created until you type `/create` and then
exactly `yes`.

Two starts exist. By default you describe an idea. With `--suggest` the analyst
proposes at most 5 candidates from the open tickets, unfinished runs and reported
usage totals; a candidate that cites a `#N` not in those facts is dropped, and
the one you pick prefills the draft. Before drafting, up to 5 open or closed
issues with similar words are listed so you can stop with `/quit` if the work
exists. Then `new` asks for the outcome, who benefits, the boundaries and the
Size (XS, S, M or L), and the analyst fills the remaining fields. Size XL (Epic
work) is refused.

The preview shows the five sections and every problem that blocks creation:
empty Summary, Scope or title, a Component count the Size does not allow
(exactly one for XS and S), allowed files missing, absolute or containing `..`,
a milestone, parent or blocker that does not exist, or a blocker that is
closed. The parent defaults to #1; `--parent N` overrides it. The assignee
defaults to the authenticated login; `--assignee` overrides it.

- XS and S: Analysis is `No analysis needed.`, Solution holds the decision and
  links the uploaded `<N>-solution-1.md` scope, and one Component is set.
- M: Summary and Scope are filled, Analysis and Solution stay `_Pending._` and
  Components stay unset; no scope is attached. L is created as a Question in the
  same way.
- Every ticket gets Status S0, the Size and the Priority. Every ticket gets a
  `<N>-conversation.md` conversation attachment with your verbatim turns, the
  final preview and the decision. `/create` states that it will be uploaded.
  Strings matching GitHub tokens, `sk-` keys, AWS access key ids and `Bearer`
  tokens are replaced with `[redacted]` in the draft and the attachment; other
  secrets are not detected, so do not type them.
- On success it prints the ticket URL and `next: fugue run N`. It never starts
  the run.

In the session, `/help`, `/show`, `/set FIELD TEXT`, `/skip`, `/save PATH`
(a transcript that never overwrites a file), `/create` and `/quit` are
available; other text answers the question or asks the analyst for a change.
A field you wrote with `/set` or an answer is yours: an analyst change to it is
ignored with `kept your wording for FIELD`. Each analyst call is read-only in
the repository, uses the analyst role's medium-size model, sees the draft, the
step and bounded facts rather than the whole history, and at most 20 calls are
made per session. A usage record is shown as reported values, not a bill. When
the analyst is unavailable, limited, times out, fails, lacks read-only mode or
structured output, or cites an unknown `#N`, the reason is printed and you
continue by hand.

The draft is saved after every step in `drafts/ID.json` in the state directory
with mode 0600 and holds only the draft. End of input or `/quit` saves it and
creates nothing; `fugue new --resume ID` continues. An unknown or unreadable id
exits with 2 naming the id. Creation is journalled: after an interruption,
`--resume ID` finds the issue by its marker and performs only the remaining
steps. If the attachment upload fails the ticket exists without a scope link, so
`fugue run` blocks with `A scope attachment is required` until
`fugue new --resume ID` completes it.

Exit codes: 0 created or ended without creating, 2 usage or an unknown draft,
4 a failure (the draft stays saved), 6 interrupted.

## Terminal output

On a terminal, output uses colour and a single run shows a live spinner with
the current stage. When output is piped or captured, or `TERM=dumb` is set, it is
plain text with one line per stage. Set `NO_COLOR` to turn colour off; meaning
is always in the text (`error:`, `warning:`, `next:`). `COLUMNS` sets the width;
tables wrap without cutting run ids or ticket numbers. A batch never uses the
spinner, and its lines keep their `[#N]` prefix.

The follow-up question in `fugue question` streams the answer as it arrives.
The terminal layer shows analyst replies as rendered Markdown with colour on
a terminal, or as plain text when output is piped, `NO_COLOR` is set,
`TERM=dumb` is set or the terminal is narrower than 60 columns.
The chat component also understands `/help` (list commands), `/save PATH`
(write the transcript to a new UTF-8 file; an existing file is never
overwritten) and `/quit`. End a line with a backslash to continue it on the
next line. End of input also ends a chat.

## Select a solution

A Question with pending Analysis starts an interview with the analyst. Discuss
intent, users, boundaries, failure cases, constraints and code touched; the
analyst offers rough directions before writing Analysis options. When the
analyst has enough information, it can signal this with a marker on its own
final line. The marker is removed before the reply is shown or stored. After
the reply, an interactive terminal shows the confirmation prompt
`Start the analysis?`, defaulting to No. Yes finishes the interview, publishes
Analysis and opens the selection walkthrough; the interview record states
that `person and analyst` ended it. No continues the interview; the confirmation
prompt appears again only after another analyst signal.

`/proceed` remains an alias for ending the interview directly and publishing
Analysis. Without an interactive terminal, no confirmation prompt is shown;
use this alias to end the interview.
`/save PATH` saves the rendered conversation to a new file; `/quit`, end of input
or interrupt leaves the interview open without publishing or changing Status.
Run `fugue question N` again to continue with the earlier turns. At the turn
limit only `/proceed`, `/save` and `/quit` accept further input.

`fugue question N --automatic` uses a stand-in to answer from Summary, Scope,
GLOSSARY.md and the conversation only. Stand-in turns are labelled automatic.
It proceeds to Analysis on the stand-in's request or the turn limit and stops
waiting for human selection. It never selects an option. Missing requirements,
consent, credentials, billing or merge approval require human input (exit 3);
continue with `fugue question N`. Provider failure exits 4, provider limiting 5,
and interruption 6. Invalid `--automatic` combinations (`--select`, a ticket
that is not a Question, or existing Analysis options) exit 2.

The private local interview record is stored at
`<state_dir>/interviews/<owner>__<name>__<N>.json` with mode 0600. Only a finished
conversation is uploaded as `<N>-conversation.md`, containing verbatim turns,
mode, finished date and who ended it, with known token patterns redacted. Details
links the verified attachment, marking automatic interviews. The whole interview
record and its usage are never uploaded. `fugue resume` directs unfinished
interviews back to `fugue question N`.

Browse a Question with existing Analysis options using the selection walkthrough:

```sh
uv run fugue question 47
```

On an interactive terminal without `--select`, a choice prompt lists each
option, marking the recommended option `(recommended)` when the Recommendation
names `Option N`. The menu also offers Compare options, Ask a follow-up,
Select an option and Quit. Up and Down move between entries, Enter selects,
Esc cancels the prompt and Ctrl-C leaves the walkthrough. On terminals that
report the mouse, you can also select an entry with the mouse; keys always work.

Select an option opens a list of options, then a confirmation prompt naming
the chosen option, defaulting to No. After confirming, `Reason (optional):`
asks for a reason; Enter keeps the default reason. The decision is recorded
once. Declining or quitting records nothing. After a recorded selection, the
output ends with `next: fugue children N`; nothing is created.

When input or output is not a terminal, `TERM=dumb` is set or the prompt
library cannot start, the walkthrough falls back to typed commands: a number
opens an option, `c` compares, `a` asks a follow-up, `s N` selects and `q` quits.
End of input ends without changes.

Alternatively, `--select K --reason R --by B` records the decision without
any prompt:

```sh
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
uv run fugue review start 1
```

Review evaluates the pull request head. A blocking finding blocks merge; a
non-blocking finding does not. When a decision is requested, use the finding
indices printed for that review. For example, if findings 1 and 2 exist:

```sh
uv run fugue review decide 1 --accept 1 --reject '2:Outside the selected scope'
```

Accept findings to request repair, or reject them with a reason. Repeat
`--accept` or `--reject` for additional indices. Indices must be positive and
cannot be repeated in the same decision. Do not apply these sample decisions
without checking the actual findings. Repair is bounded by the configured
repair limit; changed code needs verification and review of its new head.

## Request approval and merge

After the gate, CI and required review are satisfied:

```sh
uv run fugue approval request 1
```

Inspect the requested head SHA and the results. The developer can then record
approval and explicitly merge:

```sh
uv run fugue approval record 1 --reason 'Gate, CI and required review are satisfied' --by developer
uv run fugue merge 1
```

`approval record` requires a non-empty reason; `--by` defaults to the OS user. Approval
is bound to the exact head commit SHA. A new commit, including conflict
resolution, invalidates it and requires a new request and approval. The example
reason is a decision you must substantiate. `merge` merges the approved head and
closes the ticket subject to workflow checks; parents wait for closed children.

## Recover and measure

```sh
uv run fugue resume 1
uv run fugue report usage --by role
uv run fugue report usage --by provider --ticket 47
uv run fugue report usage --by run
uv run fugue report usage --by ticket
uv run fugue report timing
uv run fugue report timing 1
```

`resume` continues the same run from its last unfinished stage. Fugue can
reconcile recorded operation intents with external state after interruption,
reusing existing objects. Preserve the state directory and inspect the problem
before retrying. Resume does not grant solution selection or merge approval.

Usage records contain reported tokens, elapsed time and, where available, cost
estimates. `n/a` means the provider did not report the value. Estimates are never
a bill. `report timing` reports stage attempt durations in seconds; an unfinished attempt
shows `n/a`. See [limitations](limitations.md) for exit codes and recovery advice.
