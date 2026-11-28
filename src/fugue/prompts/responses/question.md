<!-- The sections the interview adds to a Question or an Incident, which records the decisions. It carries no solution file: its M tickets build the logic, and they cite the decisions by id. The ticket is the overview, so write the decision and what follows from it. Follow this template exactly: keep every heading, in this order, and replace each <placeholder>. Keep paragraphs to three lines or fewer. Use short bullets, bold labels, tables and callouts. Use no emojis. Sections marked optional may be left out when they do not apply. -->

## Analysis

> [!IMPORTANT]
> **Decision.** In the context of <the situation>, facing <the problem>, we recommend <the option> to achieve <the quality>, accepting <the downside>.

### Where we are

- <A fact that matters to the decision, with `path:line` and marked verified or unverified, and its consequence. A Question has no solution file, so its facts live here, and the children read them.>

### Drivers

<!-- optional -->

- <A criterion that decides between the directions.>

### Direction 1: <name>

**You get**

- <Result.>

**Changes**

- <The areas that change, in words.>

**Watch out**

- <Risk or limitation.>

<!-- Add one block per direction, and the direction of doing nothing when it is plausible. Include the comparison table before the directions only when there are two or more: one row per driver, one column per direction. -->

### Recommendation

Direction <N>: <one sentence of reason.>

## Solution

<!-- Fill after the developer selects. Until then write: _Not selected yet._ -->

> [!IMPORTANT]
> **Decision.** In the context of <the situation>, facing <the problem>, we decided <the option> to achieve <the quality>, accepting <the downside>.

**Selected by** <the developer> on <date>: <reason.>

**Agreed constraints**

<!-- One line per decision, each with an id, worded so that a child ticket can quote it as: Agreed in #<N>: C2. -->

- **C1.** <A decision, as a constraint.>

**Consequences**

- <What follows from the decision, good or bad.>

## Children

<!-- optional -->

<!-- The M tickets that build on the decision. Fill after they are created; until then leave this section out. -->

| Ticket | Title | Type | Builds on | Carries |
| --- | --- | --- | --- | --- |
| #<N> | <title> | <Feature, Bug or Maintenance> | <earlier logic ticket, or none> | <the constraint ids it carries out> |

## Details

**Key points**

- <An important finding the reader must not miss.>
- <An important finding the reader must not miss.>

**Links:** <the conversation, when there was an interview>
