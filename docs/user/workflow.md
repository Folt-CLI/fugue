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

Reporting and setup commands use grouped names in 0.3.0. Use the new
invocations below; arguments and options follow the new command name. The old
invocations remain as deprecated aliases until 0.4.0, as do the earlier review,
decision, approval and merge commands, which `run` now covers.

| Old invocation | New invocation |
| --- | --- |
| `fugue status` | `fugue report runs` |
| `fugue usage` | `fugue report usage` |
| `fugue timing` | `fugue report timing` |
| `fugue overview` | `fugue report overview` |
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
On a Question or Epic it works the children on one feature branch; see
[Run a Question or Epic on a feature branch](#run-a-question-or-epic-on-a-feature-branch).
These are alternative starting commands. They can invoke providers and mutate
Git, the filesystem and GitHub. The workflow validates the ticket, uses a
worktree, implements the selected scope, verifies changes and delivers a pull
request. A ticket whose Size analyses in full (XS, S, M and L by default) is analysed first; it stops for solution selection when a trigger fired or its Size rule leaves the selection to you.
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
  Status S0 or S3 (XS/S) or S3 (M/L), is not a Question, Epic or XL and has no open
  blocker.
- A ticket is waiting for a decision when it is an M/L ticket, a Question or an XS/S ticket parked at S1 with
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
`fugue run` analyses the ticket in full when its Size rule says so (XS and S by default); otherwise it blocks with
`A scope attachment is required` until `fugue new --resume ID` completes it.

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
This mode shows no prompts.
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

During an interview, the analyst asks through interaction requests shown as
select prompts for one choice, checkbox prompts for multiple choices,
confirmation prompts for Yes or No, typed-answer prompts for free text and
information prompts for messages to acknowledge. In the prompt chooser, Up and
Down move between entries, Space toggles checkboxes, Enter accepts and Esc
cancels. Keys always work; mouse behaviour depends on the terminal and the
prompt library.

On an interactive terminal without `--select`, the selection walkthrough shows
a compact summary of the Analysis. Choose Show full analysis to read the full
Analysis. A choice prompt lists each option; when the Recommendation names
`Option N`, that option has a `(recommended)` badge and is the default. The menu
also offers Compare options, Ask a follow-up, Select an option and Quit.
Ctrl-C leaves the walkthrough.

Select an option opens a list of options, then a confirmation prompt naming
the chosen option, defaulting to No. After confirming, `Reason (optional):`
asks for a reason; Enter keeps the default reason. The decision is recorded
once. Declining or quitting records nothing. Only the recommended option has an
implementation scope when the Analysis is published. Selecting another option first asks
the analyst to scope it: the scope is uploaded, verified and linked in that option's block,
and only then is the selection recorded; if scoping or the upload fails, nothing is
recorded. After a recorded selection,
a child proposal is derived read-only from the selected scope, Analysis and
interview turns. Review its table of keys, titles, issue types, Sizes, priorities,
Components and blocked-by keys. The Question is the parent; its milestone and
first assignee are required. Missing fields or an unavailable analyst leave the
decision recorded and point to `fugue children FILE`, the manual TOML path.

The review menu offers Edit a child, Remove a child, Add a child, Confirm and
create, and Save and quit. Typed input accepts `e KEY FIELD TEXT`, `r KEY`, `a`,
`yes`, and `q`. List fields accept JSON arrays or items separated by `|`. Add
asks for a JSON object with `key`, `title`, `issue_type`, `size`, `priority`,
`components`, `summary`, `scope`, `agreed` and `blocked_by`. A child carries its outcome in
`summary`, its numbered scenarios in `scope` and the parent's decisions as constraints in
`agreed`; it has no scope file, because an S or XS child is analysed in full once it is ready. An
optional `scope_text` pins a hand-written scope instead: use `e KEY scope_text @PATH` to
set it from a file (pasted text is also accepted). Removing a child reports dropped blocked-by references.
Validation problems must be resolved before creation is offered.

Creation requires the literal `yes` in typed input or a confirmation defaulting
to No in the menu. Quitting, declining, Esc, Ctrl-C and end of input save the
proposal without creating children. Resume with `fugue question N`. Once
creation begins, the saved proposal is locked against edits; confirming it on
resume completes interrupted operations without duplicate children. Completed
proposals display their existing child numbers. Creation prints each key and
issue number, then `next: fugue run N` for the first ready child. `fugue children`
with a TOML file remains available as the manual path.

With piped input or output, `NO_COLOR`, `TERM=dumb`, a terminal narrower than
60 columns or a prompt library that cannot start, prompts fall back to numbered
lines. Type a number for a single choice or comma-separated numbers for
checkboxes. The walkthrough accepts typed commands: a number opens an option,
`c` compares, `a` asks a follow-up, `s N` selects and `q` quits. End of input ends
without changes.

Alternatively, `--select K --reason R --by B` records the decision without
any prompt. For a Question it ends with `next: fugue question N`; run that
command to review the child proposal:

```sh
uv run fugue question 47 --select 1 --reason 'This scope meets the requirements' --by developer
```

The same command selects the solution for an analysed XS, S, M or L ticket of any
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

## An M Question answers itself

A Question whose Size analyses in full and selects by policy (M by default) needs no
interview. `fugue run N` or `fugue question N` on one does this in order:

1. Run the analysis, which checks the four triggers.
2. With no trigger fired, record the recommendation as selected by policy, with a comment
   saying so, and move the Question to S3. A fired trigger stops here with
   `waiting_for_selection`; choose with `fugue question N` as for any Question.
3. Ask the analyst for the children and create them at once, without a `yes`. The proposal
   is saved first; if it is invalid the run stops with the problem, and `fugue question N`
   shows the saved proposal for editing. Children already created are never duplicated.
4. `fugue run N` then goes on to run those children on the Question's feature branch.

Inside the run of its parent, an M Question with an open blocker is not answered yet: it waits until its sibling blockers are merged into the feature branch and its outside blockers are closed, and is reported as waiting for them; see [Analysis batches](#analysis-batches).

The Question needs a milestone and an assignee, as for any child proposal. An L Question is
never answered this way: it keeps its interview and its confirmation.

## Review, approval and merge

Review, repair, approval and merge are stages of the run, not commands you
start. `fugue run N` reviews the pull request head, repairs accepted blocking
findings within the configured repair limit, waits for CI and then stops at the
merge approval. A blocking finding blocks merge; a non-blocking finding does
not. Changed code is verified and reviewed again on its new head.

Merge approval is bound to the exact head commit SHA. A new commit, including
conflict resolution, invalidates it, so the run asks again for the new head.
Waiting is not consent: the run exits with code 7 and prints the approval it
needs, and approval stays a decision of the developer.

For a single ticket the run waits at `waiting_for_merge_approval`. Record the
approval with the command the run prints (a reason is required and must be a
decision you can substantiate; `--by` defaults to the OS user), then run the
ticket again. The run merges the approved head and closes the ticket subject to
workflow checks. Parents wait for closed children.

The earlier review, decision, approval and merge commands remain only as
deprecated aliases until 0.4.0. Do not use them as the normal way to review,
approve or merge.

## Run a Question or Epic on a feature branch

```sh
uv run fugue run 32
uv run fugue run 32 --authorise
```

When ticket 32 is a Question or an Epic, `run` works its open children on one
[feature branch](../../GLOSSARY.md#feature-branch) and delivers them to
`develop` with a single
[feature pull request](../../GLOSSARY.md#feature-pull-request).

1. The run creates `feature/32-<slug of the title>` from the current head of
   `develop` and pushes it. Running the parent again reuses the branch, and an
   existing branch of that name on `origin` is reused too.
2. Each child gets its own worktree and branch, started from the current head
   of the feature branch, so it already holds every child merged before it. Its
   pull request targets the feature branch, not `develop`. It is implemented,
   gated, reviewed and checked by CI as a single ticket is.
3. When a child is green and reviewed (every finding decided), its pull request
   is merged into the feature branch with a merge commit: an
   [integration merge](../../GLOSSARY.md#integration-merge). Only then do the
   children it blocks become ready.
4. A child is ready when every open blocker that is a child of the same parent
   is merged into the feature branch. A child blocked by two siblings waits
   until both are merged. A blocker that is not a child of this parent and is
   still open blocks the child with a problem; a closed or Done blocker never
   blocks.
5. When every child is merged, the run opens the feature pull request from the
   feature branch to `develop`, titled in the parent's commit subject format
   (for example `Question[#32]: Deliver the feature`), listing each merged
   child and its pull request, with `Closes #N` for each child. It waits for
   green CI, runs the gate on the feature head and stops at
   `waiting_for_merge_approval`.

For example, child 33 has no blocker and children 34 and 45 are both blocked
only by 33. Child 33 runs first and is merged into the feature branch; then 34
and 45 both start from the feature head, which holds 33.

### Parallel children

`limits.parallel_children` (default 1) is the most children worked at once.
With 1 the children run one at a time. A child starts only after its open
blockers; among the children that are ready, the one with the most urgent
Priority (Urgent, High, Medium, Low) starts first, and ties go to the lower
ticket number. A blocker does not take on the Priority of the children that wait
for it. With 2 in the example, 34 and 45 run together. Integration merges are
made one at a time. When a sibling was merged first, the later child's branch
is no longer up to date with the feature branch: the run merges the feature
branch into it with a merge commit (not a rebase, so the reviewed commits keep
their identity), runs the gate, pushes, and then reviews and checks CI again on
the new head before it merges that child. A merge conflict stops that child at
`blocked` with a problem naming the branch and its worktree; resolve the merge
there, commit it and run the parent again. The other children continue.

A child that fails, is blocked or waits for a decision stops only the children
that depend on it; independent children still finish. The run ends when no
further child can start and prints every child's state: merged into the
feature branch, its run state with a `next:` line, or not started with the
blockers it waits for. A Question child without an answer, or a child of any Size
whose analysis parked it without a selected solution, is such a stop: answer it with `fugue question N`,
then run the parent again. Running again after an interruption continues each
child where it stopped and never creates a second branch, pull request or
merge.

### Analysis batches

A child is analysed only when it is ready: every sibling blocker is merged into the feature branch and every outside blocker is closed. Nothing is analysed on code that does not exist yet, so a blocked child, or a blocked M Question, waits and is reported as waiting for its blockers, and is analysed in a later step of the run, or a later run, once they are merged.

The ready S and XS children that have no selected solution are analysed together in an [analysis batch](../../GLOSSARY.md#analysis-batch): one analyst call for up to `limits.analysis_batch_size` (default 5) children, taken by Priority (Urgent, High, Medium, Low), then ticket number. More ready children form further batches, and children that merges make ready form the next batch. Each child gets its own complete analysis, of the same shape and depth as an M's, because no review follows S and XS. Each child's result is checked on its own: a child whose analysis is missing or invalid is analysed again alone, and the others are kept. Usage is recorded once for the batch call, on the run of its first child.

Each child is then published on its own ticket: Analysis and Solution inline, the `<N>-code-findings.md` and scope attachments and the four trigger checks. With no trigger fired, the recommendation is selected by policy and the child is implemented from its selected scope, like an M but without review; its Status goes S0 -> S1 -> S3 -> S4 -> S5. A fired trigger parks only that child at `waiting_for_selection`; the rest of the batch goes on. M and L children are never batched: each is analysed by its own agent when it is ready. A lone `fugue run N` on one S or XS ticket is a batch of one.

### Approvals

Each integration merge needs a merge approval bound to the child pull
request's exact head SHA. Without `--authorise` the run stops each green,
reviewed child at `waiting_for_merge_approval` and prints the command to record
the approval: `fugue approval record RUN --reason 'reason text'`, then
`fugue run 32` again. The next run merges the approved child into the feature
branch and continues with the children it unblocks. A new commit on the child,
including the merge of a moved feature branch, voids the approval and the run
asks again.

`--authorise` is your explicit consent, for that invocation only, to let the
run record those approvals itself and make each integration merge as soon as a
child is green and reviewed, each at its exact head SHA. The approvals record
the authorising identity (`--by`, default the OS user) and the reason
`authorised for this run`.

The feature pull request is never covered by `--authorise`. When it is green
and gated the run stops at `waiting_for_merge_approval` with the run id of the
feature pull request. Record your approval of its exact head with
`fugue approval record RUN --reason 'reason text'` and run `fugue run 32`
again: the run merges the feature pull request into `develop` with a merge
commit, then closes each child with `Disposition: Merged` and `S5: Done`,
comments on it, and removes the worktrees and the child and feature branches.
Children stay open until then, because GitHub closes issues only for merges
into the default branch; the run store records which children are merged into
the feature branch. The parent closes under the existing rules once its
children are closed; the run does not close it. `fugue merge` and
`fugue resume` on a child or feature pull request run point back to
`fugue run 32`.

## Recover and measure

`fugue report sizes` shows, per run, the Size it started with, the Size it was re-sized to (if any), whether the developer or the Size rule selected the solution, how many repairs and review rounds it needed, and its state. Use it to see whether the Size boundaries save work: many re-sizes from S, or many repairs on M, mean a boundary is in the wrong place.

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
shows `n/a`. See [limitations](limitations.md) for recovery advice.

## Exit codes

The CLI maps run outcomes to these codes:

| Code | Meaning |
| --- | --- |
| `0` | Command completed normally, including a completed run or a run waiting for CI |
| `2` | Usage, configuration or input error, including an unknown run id |
| `3` | Run blocked; inspect reported problems and supply the missing prerequisite or decision |
| `4` | Run failed or an execution error occurred |
| `5` | Run provider-limited |
| `6` | Run interrupted |
| `7` | Run waiting for an answer (`waiting_for_selection`), a decision (`waiting_for_decision`) or merge approval (`waiting_for_merge_approval`) |

For several tickets the highest-precedence code applies: interrupted (6), then
failed or error (4), provider-limited (5), blocked, skipped or lock-conflicted (3),
then waiting for an answer, a decision or merge approval (7), otherwise 0.
Code 7 also applies when continuing a run with `resume`, and when a run of a
Question or Epic waits for an answer, a decision or a merge approval of a child
or of the feature pull request. A parent run reports the most severe state of
its unfinished children: failed, provider-limited, blocked, then waiting.

An exit code of zero alone does not mean the ticket was merged or that the run
completed. Check the printed state and `report runs`.
