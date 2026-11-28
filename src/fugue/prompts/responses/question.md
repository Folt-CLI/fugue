<!-- The sections an analysis adds to a Question, which records a decision. A Question carries no solution file: its child tickets carry the work, and they quote the decision. The ticket is the overview, so write the decision and what follows from it. Follow this template exactly: keep every heading, in this order, and replace each <placeholder>. Keep paragraphs to three lines or fewer. Use short bullets, bold labels, tables and callouts. Use no emojis. Sections marked optional may be left out when they do not apply. -->

## Analysis

> [!IMPORTANT]
> **Decision.** In the context of <the situation>, facing <the problem>, we recommend <the option> to achieve <the quality>, accepting <the downside>.

### Where we are

- <A fact that matters to the decision, with `path:line` and marked verified or unverified, and its consequence. A Question has no solution file, so its facts live here, and the children read them.>

### Drivers

<!-- optional -->

- <A criterion that decides between the options.>

### Option 1: <name>

**You get**

- <Result.>

**Changes**

- <The areas that change, in words.>

**Watch out**

- <Risk or limitation.>

<!-- Add one block per option, and the option of doing nothing when it is plausible. Include the comparison table before the options only when there are two or more: one row per driver, one column per option. -->

### Recommendation

Option <N>: <one sentence of reason.>

### Trigger check

| Trigger | Fired | Evidence |
| --- | --- | --- |
| Requirements materially ambiguous | <yes or no> | <One line.> |
| Meaningfully different user-visible behaviour | <yes or no> | <One line.> |
| Significant architectural commitment | <yes or no> | <One line.> |
| Destructive or hard to reverse | <yes or no> | <One line.> |

## Solution

<!-- Fill after the developer selects. Until then write: _Not selected yet._ -->

> [!IMPORTANT]
> **Decision.** In the context of <the situation>, facing <the problem>, we decided <the option> to achieve <the quality>, accepting <the downside>.

**Selected by** <the developer or the policy> on <date>: <reason.>

**Agreed constraints**

<!-- One line per decision, each with an id, worded so that a child ticket can quote it as: Agreed in #<N>: C2. -->

- **C1.** <A decision, as a constraint.>

**Consequences**

- <What follows from the decision, good or bad.>

## Children

<!-- optional -->

<!-- The child tickets that deliver the decision. Fill after they are created; until then this section is left out. -->

| Ticket | Title | Type | Size | Blocked by | Delivers |
| --- | --- | --- | --- | --- | --- |
| #<N> | <title> | <type> | <size> | <ticket or none> | <the constraint ids it carries out> |

## Details

**Key points**

- <An important finding the reader must not miss.>
- <An important finding the reader must not miss.>

**Links:** <the conversation, when there was an interview>
