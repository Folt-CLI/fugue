<!-- The solution: the complete scope of the selected option, written for an agent that cannot ask questions. Follow this template exactly: keep every heading, in this order, and replace each <placeholder>. Name symbols, not line numbers. Give exact text only where exactness is the decision: public signatures, error variants, vocabulary and output formats. Use no emojis. Sections marked optional may be left out when they do not apply. -->

# Solution: <option name>

## Allowed files

You may touch only these files.

- `<path>`

<!-- Include each file's tests. The worktree path and the branch come from the prompt, not from here. -->

## Source of truth

<!-- optional -->

<The authoritative definitions the change must agree with. Include this section when words change.>

## Vocabulary changes

<!-- optional -->

<The exact vocabulary changes. Leave a missing term unresolved; never invent one.>

## Changes

- `<path>`: <the behaviour this file must change to, in one line.>

## Scenarios

1. <The situation and the expected outcome, observable, including restart or failure cases that must be handled.>

## Do not change

- <A boundary that must be preserved.>

## Verification

| Command | Expected output | Scenarios |
| --- | --- | --- |
| `<command>` | <what it prints or returns> | <the scenarios it checks> |

## Final message

<What the implementing agent returns: the files it changed, and the result of each verification command.>
