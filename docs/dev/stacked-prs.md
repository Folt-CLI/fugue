# Stacked pull requests

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](process.md#autonomous-development-campaign-exceptions) for the campaign.

Use a stack when a ticket depends on implementation in another ticket whose
pull request is still open. Create a native GitHub stack with the `gh stack`
extension. A stack is a chain of pull requests in one repository: the bottom
pull request targets `develop`, and each later pull request targets the branch
below it. Each pull request shows its own layer's diff. Cross-fork stacks and
GitHub Desktop are not supported. See GitHub's [about stacked pull
requests](https://docs.github.com/en/pull-requests/get-started/about-stacked-prs).

Keep one worktree per ticket. Stacks may span linked worktrees, but `gh stack
rebase` updates only clean worktrees. Stack state is shared
by worktrees in the clone; do not run other Git tools concurrently with stack
mutations.

## Prerequisites

Install GitHub CLI 2.90.0 or newer, Git 2.36 or newer, and the
`github/gh-stack` extension. Its skill is optional. Check that stacked pull requests are enabled for this
repository. `gh stack` exit code 9 means the feature is not enabled.

## Create and submit a stack

Start the bottom branch from `develop` and add each dependent ticket's branch
above it. Use the normal issue branch name `<type>/<issue-number>-<short-slug>`.
For example:

```sh
gh stack init --base develop feature/22-foundation
gh stack add feature/23-dependent
gh stack submit
```

`gh stack init` can also adopt existing branches, listed bottom to top. Use
`gh stack link` to link existing branches or pull requests into a stack. Use
`gh stack view` to inspect its order and pull request status. See the [GitHub
CLI reference](github-cli.md#stacked-pull-requests) for the supported commands.

The bottom pull request targets `develop`; `gh stack submit` sets the chained
bases for the upper pull requests. State the stack position in each pull
request using the [pull request template](../../.github/PULL_REQUEST_TEMPLATE.md).

## Keep the stack current

Fetch remote changes, then use `gh stack rebase` to rebase the stack's branches.
Run the local suite gate in [Testing](testing.md) on every affected branch, then
use `gh stack push` only when all pass. It uses per-branch `--force-with-lease`
checks and is not atomic. Stop and report if
the local and remote stacks have diverged, a push is rejected, or a rebase
conflict cannot be resolved without discarding remote work. Otherwise, resolve
rebase conflicts, stage the resolution with `git add`, and continue with
`gh stack rebase --continue`.

CI and branch protection on the bottom pull request's base apply to every
layer. Check every pull request's status as work advances.

## Merge and ticket status

Only the developer merges a pull request. `gh stack merge` merges every stack
member up to the selected PR in one atomic, all-or-nothing operation; with no
argument it uses the current stack, or pass a PR number to merge up to that PR.
Through the GitHub UI, merge one layer at a time from the bottom up. Do not run
`gh stack merge` or a merge API unless the developer instructs you to merge
that stack.
The synchronous pull request merge API does not support stacks; the stack merge
uses GitHub's asynchronous merge endpoint. GitHub's documentation describes
automatic retargeting of upper pull requests after a bottom pull request merges
with merge commit, squash or rebase methods; the about-stacked-prs page lists all
three methods as supported. The merge-commit case has not been observed in this
repository.

Update the Fugue project status separately for each ticket, following [Ticket
planning](ticket-planning.md). A shared stack does not change ticket ownership
or status.

## When gh stack is unavailable

Fugue stops before creating a dependent worktree or opening a pull request when
its stack probe reports `gh_too_old`, `git_too_old`, `extension_missing` or
`feature_disabled`. Report the reason and stop. Do not emulate a stack with
chained plain pull request bases. Install the required versions and extension,
or enable the feature for the repository, then resume.

The stack probe checks CLI and Git versions, the installed extension and
`gh stack view` in the repository. Exit code 2 means the current branch has no
local stack tracking; it allows initialization after repository support is
confirmed. Exit code 9 establishes `feature_disabled`. The probe independently
reads `GET /repos/{owner}/{repo}/stacks` for the configured repository; HTTP 404
also reports `feature_disabled`, while permission and other API errors stop
the run. Missing local tracking never serves as evidence of repository support.
An unexpected command failure also stops the run. A successful probe establishes
availability for that invocation; it does not establish merge behavior or
review permissions. This implementation has been verified with fakes; live
repository enablement has not been exercised.

A dependent run requires exactly one open blocker with an open pull request.
It records that branch as its base, fetches it and creates the dependent
worktree from its remote ref. Native `gh stack submit` publishes the layer;
Fugue records its stack position and the lower pull request in the PR body.
Restarting reuses the recorded base, existing worktree, branch and open PR.
Divergent local and remote branches stop delivery before submission. Independent
tickets retain the configured base (`develop` by default).
