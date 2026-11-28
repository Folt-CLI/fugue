<!--
The solution: the complete plan for one option, written for an agent that cannot ask questions.
Follow this template exactly: keep every heading, in this order, and replace each <placeholder>.
Name symbols, not line numbers. Give exact text only where exactness is the decision: public
signatures, error variants, vocabulary and output formats. Use no emojis.
Sections marked optional may be left out when they do not apply.
-->

# Solution <k>: <option name>

## Allowed files

You may touch only these files.

- `<path>`

<!-- Include each file's tests. The worktree and the branch are given elsewhere, not here. -->

## Source of truth

<!-- optional -->

<The authoritative definitions the change must agree with. Include this section when words change.>

## Vocabulary changes

<!-- optional -->

<The exact vocabulary changes. Leave a missing term unresolved; never invent one.>

## Changes

- `<path>`: <the behaviour this file must change to, in one line.>

## In-scope scenarios

1. <The situation and the expected outcome, including restart or failure cases that must be handled.>

## Out-of-scope scenarios

<!-- optional -->

1. <A situation that is deliberately not handled.>

## Do not change

- <A boundary that must be preserved.>

## Verification

| Command | Expected output | Scenarios |
| --- | --- | --- |
| `<command>` | <what it prints or returns> | <the in-scope scenarios it checks> |

## Final message

<What the implementing agent returns: what it changed, and the result of each verification command.>

## Notes for the orchestrator

<!-- optional -->

<Only what the orchestrator needs. This section is removed before the implementing agent reads
the solution.>
