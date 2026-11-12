# Issue format

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](process.md#autonomous-development-campaign-exceptions) for the campaign.

The issue body is the shared record of why work exists, what it includes, and
how completion will be judged. Keep it short enough to scan. Put larger research
or design material in a linked document; comments should remain brief.

## Work items

Terms are defined in [GLOSSARY.md](../../GLOSSARY.md).

Use the forms under `.github/ISSUE_TEMPLATE`, one per issue type: Task, Bug,
Feature, Question, Epic, Maintenance, Documentation, Incident and Test. Follow the
[working templates](working/README.md) for what each size contains; the table
below describes sections when their stage applies.

| Section | When to fill it | Purpose |
| --- | --- | --- |
| Summary | At creation | State the current situation or user need, expected outcome, and who benefits. |
| Scope | At creation | Name affected area and what is in and out. |
| Analysis | During refinement | Record current behavior, cause when relevant, constraints, compatibility, and viable options. |
| Solution | At creation for S/XS; after the developer chooses for M/L | S/XS: record the enabling Question's decision and required Codex scope. M/L: summarize the selected solution, affected areas, and reason for choosing it. |
| Details | After investigation | Link the code findings attachment and summarize two to four key facts. |

For a defect, describe what happens and what should happen without presuming a
cause. For new work, describe the user need and the result. Keep `In scope` and
`Out of scope` explicit. Avoid unresolved questions in an issue marked ready to
implement; resolve them during refinement or create a discovery issue.

## Analysis and solution selection

Details holds a link to the full code findings and two to four key points the
reader must not miss. State the findings and their implications; do not use this
section as a list of file paths or routes. Exact code references belong in the
attachment. Leave Details pending until investigation has produced findings.

```markdown
## Details

**Code findings:** [<N>-code-findings.md](<attachment-url>)

**Key points**
- <important finding>
- <important finding>
```

The orchestrator, not Codex, investigates and writes Analysis inline in the
ticket, as if the developer had already asked for context, comparison and the
next step. The developer selects the solution; a recommendation is not a
selection.

XS and S tickets have no Analysis; write their Solution in the ticket at creation,
from the enabling Question, with no solution selection.

S always has a scope attachment; self-descriptive XS may skip it. A Question's
answer does not replace a missing Solution or scope. XL has no analysis.

1. Record facts with exact `file:line` references in `<N>-code-findings.md`,
   linked from Details.
2. Write each viable solution as a complete Codex scope of work in
   `<N>-solution-<k>.md`, using the layout below. Codex must be able to run the
   selected scope as written, without another prompt after selection. Offer
   alternatives only when there is a real trade-off.
3. Write Analysis inline using the layout below. Discuss the options with the
   developer.
4. Wait for the developer's choice before implementation. Leave Solution pending.
   If no option fits, revise the scopes.
5. Mark the chosen attachment **[Selected Solution]**, record the decision in the
   issue, and fill Solution from it.

### Analysis layout

Use this order in the ticket's `## Analysis` section:

1. A `> [!IMPORTANT]` callout stating the decision in one sentence.
2. `### Where we are`: bullets of facts and their consequences, followed by
   **Out of scope** and **Terms**. Use terms exactly as defined in
   [GLOSSARY.md](../../GLOSSARY.md); list a missing term as unresolved, never
   invent it.
3. A comparison table when there are two or more options. Use rows such as rule
   duplication, diff size, main risk and reader.
4. One `### Option N: <name>` block per option, with bold labels **You get**,
   **Changes**, **Watch out** and **Codex scope**. The last links to its scope
   attachment.
5. `### Recommendation`: one option and one sentence of reason.
6. A `> [!TIP]` callout telling the developer to reply with the option number.

Use short bullets, bold labels, tables and callouts. No paragraph may exceed
three lines. Use no emojis. A short skeleton for two options:

```markdown
## Analysis

> [!IMPORTANT]
> <Decision in one sentence.>

### Where we are

- <Fact and its consequence.>

**Out of scope**

- <Excluded work.>

**Terms**

- <Glossary term and its exact meaning, or missing term listed as unresolved.>

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

**Codex scope:** [<N>-solution-1.md](<attachment-url>)

### Option 2: <name>

**You get**

- <Result.>

**Changes**

- <Change.>

**Watch out**

- <Risk or limitation.>

**Codex scope:** [<N>-solution-2.md](<attachment-url>)

### Recommendation

Option <N>: <one sentence of reason>.

> [!TIP]
> Reply with the option number to select its Codex scope.
```

### S and XS Solution layout

Write a short, readable Solution at creation, in the style of Analysis. Use the
order below; **Agreed in #N** records what the parent Question decided.
The solution comes from that Question; it is not selected on the S/XS ticket.

**Codex scope** links the complete `<N>-solution-1.md` scope, using the layout
below. Attach with gh-attach and verify by downloading and comparing at creation.
S always has this file; XS may skip it when the ticket is self-descriptive.

```markdown
## Solution

> [!IMPORTANT]
> <Decision in one sentence.>

**Agreed in #N**

- <What the parent Question decided.>

**You get**

- <Result.>

**Changes**

- <Change.>

**Not changed**

- <Excluded work.>

**Watch out**

- <Risk or limitation.>

**Codex scope:** [<N>-solution-1.md](<attachment-url>)
```

### Codex scope layout

Each `<N>-solution-<k>.md` attachment is a complete scope of work. Use these
sections in order:

- **Standing rules:** implement only; no Git commit, push or `gh`; the exact
  worktree path and Git branch; the exact files Codex may touch; no attribution
  lines; no emojis. Require reading `GLOSSARY.md` first and using its terms
  exactly as defined, never in another sense.
- **Source of truth** and **Vocabulary changes:** include both when words
  change. State the authoritative definitions and exact vocabulary changes;
  leave missing terms unresolved rather than inventing them.
- **File-by-file changes:** give line references and the exact intent for each
  file, including affected areas.
- **Do not change:** list the boundaries Codex must preserve.
- **Verification:** give commands and their expected output.
- **Final message:** state what Codex returns.
- **Notes for the orchestrator (not for Codex):** give the Git commit plan, PR
  fields and review focus. These are instructions for the orchestrator only.

For M and L, Solution contains the selected result, not another plan document:

```markdown
## Solution

> More details are in the attachment tagged **[Selected Solution]** above.

- <what changes, one short bullet each>

**Affected areas:** <pages, routes, or components covered by the chosen solution>

**Why this one:** <one sentence>
```

Match the affected-area list to Scope: one page for a page change, a list for a
feature group, or a table for a change across the application. Copy the affected
areas from the selected solution attachment. Unselected scopes keep their
affected-area lists only in their own attachments.

Findings contain facts; solution attachments contain executable Codex scopes.
Long material stays in attachments and issue comments stay short. Name files
with the issue number so they remain identifiable.

Upload Markdown files using the `gh-attach` extension, then link their returned
URLs from the issue. See [the upload procedure](github-cli.md#markdown-attachments).
Verify the uploaded contents and issue links. Do not substitute public gists,
repository commits, or local filesystem paths for GitHub attachments. If no
signed-in browser session is available, keep upload pending and report that
specific blocker.

## Epics

An epic contains only `## Objective` followed by a quick explanation of what we
want from the release. Its title is `<version> -- Fugue`. Close it with the
Disposition issue field set to Released only after all its children are closed;
see the [XL template](working/xl-epic.md).
Place release work under its existing delivery group or Question where
applicable; otherwise default to #1 unless the developer specifies another
parent. See [workflow](workflow.md#dependencies-and-grouped-work). Use native
parent relationships; the description does not repeat them.

## Notes, attachments, and links

- Use comments for concise decisions, questions, and handoffs; update the body
  when the issue's agreed scope or plan changes.
- Link long design notes and investigation findings instead of pasting them in
  full. Keep sensitive data, credentials, and private user information out of
  issues and attachments.
- Link dependencies and related work with `#<issue-number>` references.
- Pull requests should state what changed, how it was checked, and any follow-up
  work, with links to the relevant issues.
