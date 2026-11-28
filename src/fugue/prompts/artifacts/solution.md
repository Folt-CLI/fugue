<!-- The solution: the specific plan for the selected option, written for an agent that cannot ask questions. It adds to the ticket and never repeats it: the ticket holds the overview, so do not restate its summary, its scope or its scenarios, and refer to a ticket scenario as "ticket scenario 2". Follow this template exactly: keep every heading, in this order, and replace each <placeholder>. Name symbols, not line numbers, except for a fact you checked. Give exact text only where exactness is the decision: public signatures, error variants, vocabulary and output formats. Use no emojis. Sections marked optional may be left out when they do not apply. -->

# Solution: <option name>

Implements #<N>.

## Context

<Where the change lives and how the project already does this kind of thing: the files and symbols involved and the existing code to follow, each fact with `path:line` and marked verified or unverified. Add only what the implementer could not find in a minute. When words change, state the authoritative definition and the exact vocabulary change here, and leave a missing term unresolved.>

## Changes

<!-- This says where the change is expected. It is not a limit: the implementer changes what the work needs. -->

- `<path>`: <the behaviour this file must change to, in one line.>

## Scenarios

<!-- optional -->

<!-- Only what the ticket's scenarios do not already say: failure cases, restart cases, boundaries. -->

1. **Given** <the state>, **when** <the action>, **then** <the observable result>.

## Do not change

<!-- optional -->

- <A boundary that must be preserved, such as a public interface or a format.>

## Verification

| Command | Expected output | Proves |
| --- | --- | --- |
| `<command>` | <what it prints or returns> | <ticket scenario N, or scenario N above> |
