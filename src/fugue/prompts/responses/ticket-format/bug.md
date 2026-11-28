<!-- Ticket body for a Bug. Follow this template exactly: keep every heading, in this order, and replace each <placeholder>. Keep paragraphs to three lines or fewer. Use short bullets, bold labels, tables and callouts. Use no emojis. Link long material and keep it out of the body. -->

## Summary

**Steps to reproduce**

1. <Step.>

**Actual behaviour.** <What happens. Do not presume a cause.>

**Expected behaviour.** <What should happen.>

**Who benefits**

- **How:** <The benefit.>
- **Why:** <The reason it matters.>
- **Impact on existing use:** <None, or what changes for people who use it today.>

## Scope

**Affects:** <The area the work touches.>

**In scope**

- <Included work.>

**Out of scope**

- <Excluded work, including adjacent work that is left alone.>

**Agreed in #<N>**

<!-- Only when a parent Question decided something the work must respect. State each decision as a constraint, in the Question's own words. Leave the heading out otherwise. -->

- <A decision the work must respect.>

**Scenarios**

1. <Something that can be observed when the work is done.>
2. <Something that can be observed when the work is done.>

<!-- One scenario must be the regression test: it fails before the fix and passes after. -->


## Analysis

> [!IMPORTANT]
> <The decision, in one sentence.>

### Where we are

- <A fact and its consequence.>

**Out of scope**

- <Excluded work.>

**Terms**

- <A project term and its exact meaning, or a missing term listed as unresolved.>

<!-- Include the comparison table only when there are two or more options. -->

| Comparison | Option 1 | Option 2 |
| --- | --- | --- |
| Rule duplication | <Extent> | <Extent> |
| Diff size | <Extent> | <Extent> |
| Main risk | <Risk> | <Risk> |
| Reader | <Who benefits> | <Who benefits> |

### Option 1: <name>

**You get**

- <Result.>

**Changes**

- <Change.>

**Watch out**

- <Risk or limitation.>

### Recommendation

Option <N>: <one sentence of reason>.

### Trigger check

| Trigger | Fired | Evidence |
| --- | --- | --- |
| Requirements materially ambiguous | <yes or no> | <One line.> |
| Meaningfully different user-visible behaviour | <yes or no> | <One line.> |
| Significant architectural commitment | <yes or no> | <One line.> |
| Destructive or hard to reverse | <yes or no> | <One line.> |

<!-- Fill the next two lines after the selection. Options have no scope attachment: only the selected option has one. -->

**Selected:** Option <N> by <the developer or the policy>: <reason>.

**Implementation scope:** <link to the solution of the selected option>

> [!TIP]
> Only when a trigger fired: reply with the option number.

## Solution

<!-- Fill after the selection. Until then write: _Not selected yet._ -->

> More details are in the attachment tagged **[Selected Solution]** above.

- <What changes, one short bullet each.>

**Affected areas:** <The areas the chosen solution covers.>

**Selected by:** <The developer, or the policy.>

**Why this one:** <One sentence.>

## Details

**Code findings:** <link to the findings>

**Key points**

- <An important finding the reader must not miss.>
- <An important finding the reader must not miss.>
