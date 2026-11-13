# Contributing to Fugue

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

Start with [the development process](docs/dev/process.md) and
[the current stack](docs/dev/stack.md). Use the repository issue forms and
native parents/dependencies. Keep work bounded, attach findings and solution
options when analysis is requested, and let the developer select the solution.

Follow [the workflow](docs/dev/workflow.md), [stacked pull
requests](docs/dev/stacked-prs.md) and [review](docs/dev/review.md) for issue
branches, agent delivery, stacks and reviews. Use issue-type commit subjects,
the **Summary** and **Changes** commit body and the pull request format. Record
only verification actually performed.

Keep secrets and build outputs out of Git. Document configuration using
placeholders only.

For defects, include reproduction steps and impact. A private security reporting
channel has not been configured; do not put credentials, private data or exploit
details into public issues. No repository license has been selected yet.

## Local checks

The orchestrator runs the Python gate in [Testing](docs/dev/testing.md) before
any push. The commands become runnable when the scaffold ticket lands; CI will
mirror them. Every PR runs the full required CI check.
