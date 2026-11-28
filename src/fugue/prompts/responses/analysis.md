<!-- The sections an analysis adds to a ticket, for the developer who decides. The ticket is the overview: write the decision and what it means, in words, and leave file paths, symbols and line numbers to the solution file, so nothing is said twice. Follow this template exactly: keep every heading, in this order, and replace each <placeholder>. Keep paragraphs to three lines or fewer. Use short bullets, bold labels, tables and callouts. Use no emojis. Sections marked optional may be left out when they do not apply. -->

## Analysis

> [!IMPORTANT]
> **Decision.** In the context of <the situation>, facing <the problem>, we recommend <the option> to achieve <the quality>, accepting <the downside>.

<!-- Only when a trigger fired, add this line inside the callout: **Needs your decision:** reply with the option number. -->

### Where we are

- <A fact that matters to the decision, and its consequence.>

### Drivers

<!-- optional -->

<!-- Only when there is more than one option: the three to five criteria that decide between them. They are the rows of the comparison table. -->

- <A criterion.>

### Option 1: <name>

**You get**

- <Result.>

**Changes**

- <The areas that change, in words.>

**Watch out**

- <Risk or limitation.>

<!-- Add one block per option. Offer one option when one approach is clearly right, and add the option of doing nothing when it is plausible. Include the comparison table before the options only when there are two or more: one row per driver, one column per option. -->

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

<!-- Fill after the selection. Until then write: _Not selected yet._ -->

**Selected:** Option <N> by <the developer or the policy> on <date>: <reason.>

**Consequences**

- <What follows from it, good or bad, including the follow-up tickets.>

**Solution file:** <link>

## Details

**Key points**

- <An important finding the reader must not miss.>
- <An important finding the reader must not miss.>

**Links:** <the solution file, and the conversation when there was one>
