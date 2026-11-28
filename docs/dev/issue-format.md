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
below describes sections when their stage applies. XS and S fill Summary and
Scope at creation; their analysis fills the rest, as for an M. See [XS and S body layout](#xs-and-s-body-layout).

| Section | When to fill it | Purpose |
| --- | --- | --- |
| Summary | At creation | State the current situation or user need, expected outcome, and who benefits. |
| Scope | At creation | Name affected area and what is in and out. |
| Analysis | During refinement | Record current behavior, cause when relevant, constraints, compatibility, and viable options. |
| Solution | After the selection, for XS, S, M and Questions | Summarize the selected solution, affected areas, the reason for choosing it and whether the developer or the policy selected it. Until then XS and S hold `_Not applicable._` |
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

The orchestrator, not the implementation agent, investigates and writes Analysis
inline in the ticket, as if the developer had already asked for context,
comparison and the next step. A recommendation is not a selection. The developer
selects, or the policy selects when no
[trigger](working/README.md#analysis-triggers-and-re-sizing) fires; an agent
never does.

XS and S get the same full analysis as an M, shared with other ready XS and S tickets in an [analysis batch](working/README.md#analysis-depths) but complete for each ticket: the steps below apply to each of them on its own. XL has no
analysis.

1. Record the facts that bear on the decision, with exact `file:line` references
   and each marked verified or unverified, in `<N>-code-findings.md`, linked from
   Details. Do not tour the repository.
2. Write the options inline in Analysis using the layout below. One option is
   enough when one approach is clearly right; offer alternatives only when there
   is a real trade-off. An option is a short paragraph and its trade-offs, not a
   scope.
3. Recommend one option and check the four triggers, each with its evidence.
4. Select. With no trigger fired, the policy selects the recommendation and the
   selection is recorded as made by policy with the reason. With a trigger fired,
   park the ticket and wait for the developer's choice. If no option fits, revise
   the options.
5. The recommended option's implementation scope is written with the analysis, as
   `<N>-solution-<k>.md`, using the layout below. Another option is scoped only
   when it is selected. Mark the selected attachment **[Selected Solution]** and
   fill Solution from the selection. The implementation agent must be able to run
   the scope as written, without another prompt.

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
   **Changes** and **Watch out**. Only the recommended option, or one that has
   been selected, also has **Implementation scope**, which links its scope
   attachment.
5. `### Recommendation`: one option and one sentence of reason.
6. `### Trigger check`: the four triggers, each marked fired or not, with one
   line of evidence.
7. After the selection, one line naming who selected and the reason. When a
   trigger fired, add a `> [!TIP]` callout telling the developer to reply with
   the option number.

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

**Implementation scope:** [<N>-solution-1.md](<attachment-url>)

### Option 2: <name>

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

> [!TIP]
> Only when a trigger fired: reply with the option number.
```

### XS and S body layout

XS and S are created with their outcome in the ticket body and no scope attachment. Fill Analysis, Solution and Details
with `_Not applicable._`; the full analysis replaces Analysis and Details, and the selection fills Solution.

- **Summary:** the outcome and who benefits, in one or two sentences.
- **Scope:** `In scope`, `Out of scope`, and two to five numbered scenarios, each
  stating something that can be observed when the work is done. XS needs two or
  three.
- **Agreed in #N:** under Scope, when a parent Question decided something the
  work must respect. State each decision as a constraint, in the Question's own
  words. A vague decision makes the analysis guess.
- **File hints:** optional, under Scope. They name areas, not the files the agent
  may touch; the analysis decides those.

### Implementation scope layout

The `<N>-solution-<k>.md` attachment is the complete scope of one option. A ticket whose Size rule has the `scope` depth derives the same content by scoping at run time and does not attach it. Use
these sections in order:

- **Allowed files:** the exact files the implementation agent may touch,
  including their test files, introduced by the sentence "You may touch only
  these files" and one bullet per path in backticks. Tooling reads this list as
  the boundary. The worktree path and the Git branch come from the prompt, not
  from the scope.
- **Source of truth** and **Vocabulary changes:** include both when words
  change. State the authoritative definitions and exact vocabulary changes;
  leave missing terms unresolved rather than inventing them.
- **Changes:** one line per file with the behaviour change it must make. Give
  exact text only where exactness is the decision: public signatures, error
  variants, glossary wording and output formats. Name symbols, not line numbers.
- **In-scope scenarios:** numbered; each states the situation and the expected
  outcome, including restart or failure cases the implementation must handle.
  These define done and are what the review checks.
- **Out-of-scope scenarios:** situations deliberately not handled. Both
  scenario sections are required for any scope that adds stateful or
  external-effect behaviour; a purely documentation scope may omit them.
- **Do not change:** list the boundaries the implementation agent must preserve.
- **Verification:** give commands and their expected output, mapped to the
  In-scope scenarios.
- **Final message:** state what the implementation agent returns, including the
  result for each verification step.
- **Notes for the orchestrator (not for the implementation agent):** only what
  the orchestrator needs, such as a Test-change trailer reason. Tooling removes
  this section from what the implementation agent reads. Commit plans, PR fields
  and review focus are orchestrator work and are not part of the scope.

The standing rules (implement only, no Git commit, push or `gh`, no emojis, read
`GLOSSARY.md` first and use its terms exactly) are in the implementation prompt;
the scope does not repeat them.

For M and Questions, Solution contains the selected result, not another plan document:

```markdown
## Solution

> More details are in the attachment tagged **[Selected Solution]** above.

- <what changes, one short bullet each>

**Affected areas:** <pages, routes, or components covered by the chosen solution>

**Selected by:** <the developer or policy>

**Why this one:** <one sentence>
```

Match the affected-area list to Scope: one page for a page change, a list for a
feature group, or a table for a change across the application. Copy the affected
areas from the selected solution attachment. Unselected scopes keep their
affected-area lists only in their own attachments.

Findings contain facts; solution attachments contain executable implementation scopes.
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
want from the release. Its title is `<version> - Fugue`. Close it with the
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
