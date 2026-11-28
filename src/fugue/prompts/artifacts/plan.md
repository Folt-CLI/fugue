<!-- The plan for one unit of work, written for an agent that cannot ask questions. It adds to the ticket and to the logic and never repeats them: do not restate the ticket's summary, its scenarios or the logic's constraints, and cite them as "ticket scenario 2" or "C2". Follow this template exactly: keep every heading, in this order, and replace each <placeholder>. For an XS ticket, write only Changes and Verification. Name symbols, not line numbers, except for a fact you checked. Give exact text only where exactness is the decision: public signatures, error variants, vocabulary and output formats. Use no emojis. Sections marked optional may be left out when they do not apply. -->

# Plan: <short name>

Plans #<N>, a unit of #<M>.

## Context

<!-- optional -->

<Where the change lives and how the project already does this kind of thing: the files and symbols involved and the existing code to follow, each fact with `path:line` and marked verified or unverified. Add only what the implementer could not find in a minute.>

## Changes

<!-- This says where the change is expected. It is not a limit: the implementer changes what the work needs. -->

- `<path>`: <the behaviour this file must change to, in one line.>

## Scenarios

<!-- optional -->

<!-- Only what the ticket's scenarios do not already say: failure cases, restart cases, boundaries, taken from the logic's edge cases. -->

1. **Given** <the state>, **when** <the action>, **then** <the observable result>.

## Do not change

<!-- optional -->

- <A boundary that must be preserved, such as a public interface or a format.>

## Verification

| Command | Expected output | Proves |
| --- | --- | --- |
| `<command>` | <what it prints or returns> | <ticket scenario N, or scenario N above> |
