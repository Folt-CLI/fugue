# Interface rules

These rules describe Fugue's command-line interface. The sources of truth are
[Workflow](../user/workflow.md#terminal-output) for terminal output and documented
lines, and [Limitations](../user/limitations.md#exit-codes) for exit codes.
This document specifies behaviour; it does not change command implementation.
Use [glossary](../../GLOSSARY.md) terms throughout.

The flow names below mean run (`fugue run`), review and decision (`fugue review
start`, `fugue review decide`), approval and merge (`fugue approval request`,
`fugue approval record`, `fugue merge`), new (`fugue new`), question
(`fugue question`), overview (`fugue report overview`) and status
(`fugue report runs`). Examples use illustrative ticket numbers and run ids.

## R1: Prefix vocabulary

Use `error:` for errors, `warning:` for warnings, `next:` for the next action,
`note:` for context and `problem:` for a reported problem. Meaning must remain
clear from the text without colour or decoration.

Example: `problem: A scope attachment is required`

Flows: run; review and decision; approval and merge; new; question; overview;
status.

## R2: A next step for every non-success end state

Every non-success end state must name a next step with `next:`. State the action
needed to resolve the reported problem or continue the workflow. Waiting for a
decision or merge approval also needs a next step, even when the command exits
with zero; a successful exit alone does not mean a run completed or a ticket
merged.

Example: `next: fugue question 47`

Flows: run; review and decision; approval and merge; new; question; overview;
status.

## R3: Output streams

Send results to stdout and errors to stderr. Preserve this separation on a
terminal and when output is piped or captured so consumers can read results
separately from errors.

Example: `error: unknown run id 999` is written to stderr.

Flows: run; review and decision; approval and merge; new; question; overview;
status.

## R4: Progress by stage

Progress is one line per stage in plain-text output. On a terminal a single run
may show a live spinner with its current stage. A batch never uses the spinner;
its progress lines keep their `[#N]` ticket prefix. Do not cut run ids or ticket
numbers when fitting output to the available width.

Example: `[#47] verify`

Flows: run; review and decision; approval and merge; new; question; overview;
status, whenever they report stage progress.

## R5: Choice prompts

Use a choice prompt on a terminal through the prompt chooser, and the line
chooser otherwise. Mark the recommended option explicitly in text. The prompt
chooser uses arrow keys and Enter, Esc cancels, and terminal startup failure
falls back to the line chooser. The line chooser reads numbered answers; Enter
selects the default, and `q` or end of input cancels. Confirmations use the same
choice prompt with Yes and No choices. Preserve documented defaults, including
No for `Start the analysis?`.

Example: `Start the analysis? 1. Yes  2. No (recommended, default)`

Flows: run; review and decision; approval and merge; new; question; overview;
status, wherever a choice is requested.

## R6: Plain-text fallback

Use plain text when the terminal is narrower than 60 columns, output is piped
or captured, `NO_COLOR` is non-empty, or `TERM=dumb`. Do not emit colour escape
sequences in these modes; render tables, panels and Markdown as plain text.
Keep prefixes, recommended-option markers, identifiers and next steps readable.
`COLUMNS` sets the width; 60 columns itself is not below the threshold.

Example: `next: fugue run 47` remains plain text when piped.

Flows: run; review and decision; approval and merge; new; question; overview;
status.

## R7: Named exit-code constants

Express exit codes as named constants with the following fixed values and
meanings. These names describe the contract rather than prescribing a module
or changing existing code identifiers.

| Constant | Code | Meaning |
| --- | --- | --- |
| `SUCCESS` | `0` | Command completed normally, including waiting for CI, a decision or merge approval |
| `USAGE` | `2` | Usage, configuration or input error, including an unknown run id |
| `BLOCKED` | `3` | Run blocked; a prerequisite or decision is missing |
| `FAILED` | `4` | Run failed or an execution error occurred |
| `PROVIDER_LIMITED` | `5` | Run provider-limited |
| `INTERRUPTED` | `6` | Run interrupted |

For several tickets, precedence is interrupted (6), failed or error (4),
provider-limited (5), blocked, skipped or lock-conflicted (3), then success (0).
Do not select the largest numeric code. For new, success includes ending without
creating; an unknown draft is usage, a failure leaves the draft saved, and an
interruption is interrupted. Consult the source of truth for command-specific
meanings; do not reinterpret zero as proof of a completed run or merge.

Example: an unknown run id returns `USAGE` (`2`).

Flows: run; review and decision; approval and merge; new; question; overview;
status.

## R8: Stable documented lines

Never change stable lines listed in [Workflow](../user/workflow.md) without a
note describing the change. Preserve their wording and prefixes, including
`next: fugue run N`, `A scope attachment is required`,
`kept your wording for FIELD`, `note: showing N of more than N open tickets`,
`note: could not read #N: reason` and
`note: GitHub unreachable (...); showing local runs only`. Placeholder values
such as `N`, `FIELD` and `reason` vary with the result; they are not literal
output values. Apply the same rule to the other documented lines and report
forms, not only these examples.

Example: `next: fugue run 47` preserves the documented `next: fugue run N` form.

Flows: run; review and decision; approval and merge; new; question; overview;
status.
