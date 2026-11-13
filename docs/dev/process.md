# Fugue development process

## Autonomous development campaign exceptions

For developing Fugue under `prompt.md` only:

- The orchestrator takes the developer's technical decision role: answers Questions,
  selects M/L solutions and decides review disputes from evidence. Record each
  as an **autonomous orchestrator decision**.
- The implementation agent implements and fixes; a separate reviewer session
  reviews read-only. The orchestrator does not write application code.
- The orchestrator may authorize and execute merges after the gate, CI and clean required
  review. Record authorization against the exact head SHA; any new commit
  invalidates it. No blanket auto-merge is enabled.
- Real consent, missing credentials, billing and provider restrictions remain
  genuine blockers.

These exceptions apply only to this campaign. They do not change the Fugue
product: its default stays human solution selection and human merge approval.
Ordinary repository use retains developer selection and developer-only merging
unless the developer explicitly authorizes an agent for the specific PR head.

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](process.md#autonomous-development-campaign-exceptions) for the campaign.

Issue workflow for `Folt-CLI/fugue`, Project **Fugue** (Folt-CLI Project #2).
Default branch: `develop`; release branch: `main`; release Epic: #1.
Milestones: `0.1.0` and `Backlog`. Keep process docs
under docs/dev and issue forms under .github/ISSUE_TEMPLATE. GitHub issues are
the durable work record.

Work items contain exactly Summary, Scope, Analysis, Solution and Details.
Fill Summary and Scope at creation. The orchestrator investigates and attaches
findings and separate proposals; the developer selects the solution before
implementation where analysis applies; see the working templates.
Details contains the findings attachment and two to four key points. Do not add
Acceptance criteria, Implementation plan or Closeout sections. Record delivery
and performed verification in the PR.

Use native parent links for grouping and blocked-by links for prerequisites.
Independent release work defaults to epic #1; use an existing delivery group or
Question, or an explicit developer instruction when applicable. See
[Issue format](issue-format.md#epics) for release Epics. Do not repeat a ticket's
parent in its description.

| Guide | Covers |
| --- | --- |
| [Glossary](../../GLOSSARY.md) | Process terms and unresolved product vocabulary |
| [Working templates](working/README.md) | How to work a ticket by size and issue type |
| [Workflow](workflow.md) | Issue stages, branches, commits, issue types and fields, and PRs |
| [Stacked pull requests](stacked-prs.md) | Creating, updating and merging native PR stacks |
| [Review](review.md) | Read-only agent reviews and posting findings |
| [Testing](testing.md) | Test rule, push gate, CI review, roles |
| [Issue format](issue-format.md) | Work-item and release-epic format and analysis attachments |
| [Ticket planning](ticket-planning.md) | Issue types and fields, assignment, priority, relationships, milestones |
| [Incidents](incidents.md) | Reports, evidence, linked response work and resolution |
| [GitHub CLI](github-cli.md) | Issues, native relationships, Markdown uploads and PR commands |
| [Stack](stack.md) | Confirmed technical choices and pending decisions |
| [Verification](verification.md) | Checks actually performed and their status |

[AGENTS.md](../../AGENTS.md) points future sessions to this process.
[CONTRIBUTING.md](../../CONTRIBUTING.md) is the contributor entry point.
The [working templates](working/README.md) hold the size, status and closing
rules. Use the other guides for their specific procedures.

Adapted from the Folt source process at reference commit
`198c589c3f79938915c8ab1f5d11aa94ebb7175c`; Fugue policy was decided in
[Question #2](https://github.com/Folt-CLI/fugue/issues/2), Option 1.
