# Testing

## Rule

Every ticket adds or updates the tests for what it changes, in the same pull
request. A bug fix adds a regression case that fails before the fix. The
implementing agent writes the tests. The local suite must pass before anything
is pushed. Do not add dependencies without the ticket's selected solution.
Map each test and verification step to a listed In-scope scenario; see the
[review contract](review.md#review-contract).

## Test tickets

The Test issue type is for work whose whole outcome is test cases. A framework,
helpers, runner or CI job is a Feature. Tests belonging to another change stay
in that change's ticket. Test ticket commits use `Test[#N]:`.

## Roles

Codex implements and fixes in the ticket's dedicated worktree. It does not
analyse code, reviews or CI. The orchestrator investigates, runs the gate,
reads CI and supplies findings and failing output to fix. A separate read-only
Codex session reviews the diff, changed tests and supplied CI results; it does
not run builds or checks. See the [campaign exceptions](process.md#autonomous-development-campaign-exceptions).

## Gate before push

The orchestrator runs these commands in the worktree before any push:

```sh
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv build
```

These commands exist and are mirrored by the `CI` workflow job.
Every PR runs the full required CI check; there is no PR-build skip
mechanism. This document records policy, not evidence of an executed check.

Commit and push only when all commands pass. Return failures and their output
to the implementation agent. Never push a failing gate.

## CI result in the review

After opening or updating the PR, the orchestrator waits for CI:

```sh
gh pr checks <n> --repo Folt-CLI/fugue --watch
```

For each failed job, collect its failing output:

```sh
gh run view <run-id> --repo Folt-CLI/fugue --log-failed
```

Trim to the failing step, at most the last 200 lines. Re-run a job with no clear
cause once:

```sh
gh run rerun <run-id> --repo Folt-CLI/fugue --failed
```

A second failure is a finding. Include actual results and excerpts under
**CI result** in the review prompt because the read-only sandbox has no
network. A failed, cancelled or missing required job is a High finding;
include the excerpt or state plainly that no log exists. Post the review only
after CI finishes.

## Verification in the PR

List each local command and result, and each actual CI job and result after CI
finishes. State what did not run. Do not infer platforms or check results from
this policy.

## Standard paragraph for every Codex implementation prompt

> Every ticket adds or updates the tests for what it changes, in the same pull
> request. A bug fix adds a regression case that fails before the fix. Write the
> tests and run `uv run pytest` (once the scaffold exists). Do not add dependencies
> without the ticket's selected solution. Do not analyse code, reviews or CI;
> implement and fix only. The orchestrator runs the gate before committing or
> pushing and hands you findings and failing output to fix.
