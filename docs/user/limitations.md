# Limitations and failure handling

The current Fugue release is **0.1.0**, available as an installable wheel; see
[wheel installation](getting-started.md#prerequisites). Its CLI coordinates local
Git worktrees, GitHub and the unmodified Claude and Codex CLIs; it requires those
tools and their credentials. It does not provision provider subscriptions,
GitHub Projects or repository process metadata. See
[getting started](getting-started.md) for prerequisites and
[configuration](configuration.md) for supported settings.

## Boundaries

- The configuration schema supports `claude` and `codex` providers and the
  analyst, implementer and reviewer roles. Provider capabilities and reported
  usage differ; a session can be resumed only where the provider supports it.
- Both bundled provider adapters require `subscription_login`, using your own
  sign-in to the unmodified provider CLI. The schema accepts `api_key` when
  explicitly set in TOML, but both adapters reject it when invoked. Provider
  fallback (`[[roles.<role>.fallback]]`) moves a whole call to the next listed
  provider when one is rate limited, not signed in or unreachable, but never to
  metered authentication and never carries a session between providers. Only
  structured signals count: Claude reports signed-out and outage through numeric
  API statuses; Codex has no signed-out signal, so only its rate limits and spawn
  failures switch. When every provider is unavailable the run ends
  provider-limited and `fugue resume` continues it.
- Workflow commands require a GitHub repository and Project configuration,
  ticket metadata and suitable permissions. A non-GitHub origin cannot be used
  to infer the repository; set `github.repo` explicitly when needed.
- The default gate assumes uv, Ruff, mypy and pytest. Other repositories need
  their own configured gate. Passing a local gate does not establish green CI.
- Default policy keeps solution selection and merge approval with the developer.
  A waiting state is not consent. Any new head commit invalidates approval.
- In single-account GitHub use, reviews use COMMENT because the author cannot
  approve their own pull request. A clean required review and Fugue approval
  still have distinct purposes. See [reviewer identity setup](reviewer-identity.md)
  for the separate identity setting.
- Stacks require native `gh stack`, gh >= 2.90.0, Git >= 2.36, the
  `github/gh-stack` extension and repository enablement. An unavailable stack
  probe stops dependent runs before worktree or PR creation and reports
  `gh_too_old`, `git_too_old`, `extension_missing` or `feature_disabled`.
  Fugue does not emulate stacks with chained plain PR bases. A dependent run
  requires a sole open blocker with an open PR. Divergence stops submission;
  stack merging still requires a developer instruction for that stack and head.
- Posting reviews as a reviewer app requires registration, installation for the
  repository, a saved local private key, OpenSSL and
  `github.reviewer_identity_separate = true`; see
  [reviewer identity setup](reviewer-identity.md). Replies and thread resolution
  retain their existing credentials.
- Branch protection enforcing approvals is deferred to
  [issue #78](https://github.com/Folt-CLI/fugue/issues/78).
- Follow-up hardening work remains deferred and is collected in
  [issue #53](https://github.com/Folt-CLI/fugue/issues/53).
- The run store and artifacts are local. Keep their directory to inspect or
  resume a run. Output is bounded by `max_output_bytes`; usage and cost data
  are limited to what providers report, and cost estimates are not a bill.

## Parallel runs

- Runs of one ticket are exclusive: a second `fugue run` of the same ticket in
  the same repository exits with code 3 and names the process holding the
  ticket lock. After a killed process the next run recovers the lock and
  resumes the unfinished run. If the lock file is held but its recorded process
  is not running, stop any remaining processes holding the file; never delete it.
- `fugue run` accepts several ticket numbers, runs at most
  `limits.max_concurrent_runs` at once, labels progress lines `[#N]` and prints
  a summary. A ticket with an open blocker is skipped. Skipped and
  lock-conflicted tickets exit with code 3.
- Shared Git steps in the primary checkout are serialised across processes by a
  repository lock; agent work is not.
- Concurrent runs in one process share the provider cool-down, so one run's
  limit makes the next call in another run start with the next provider. The
  cool-down is not shared across processes or kept across restarts, and there is
  no throttle or backoff. A run whose providers are all unavailable ends
  provider-limited (exit code 5) while others go on.
- `resume`, `review`, `decide`, `approve` and `merge` do not take the ticket
  lock.

## Exit codes

The CLI maps run outcomes to these codes:

| Code | Meaning |
| --- | --- |
| `0` | Command completed normally, including workflow states waiting for CI, a decision or merge approval |
| `2` | Usage, configuration or input error, including an unknown run id |
| `3` | Run blocked; inspect reported problems and supply the missing prerequisite or decision |
| `4` | Run failed or an execution error occurred |
| `5` | Run provider-limited |
| `6` | Run interrupted |

For several tickets the highest-precedence code applies: interrupted (6), then
failed or error (4), provider-limited (5), then blocked, skipped or
lock-conflicted (3), otherwise 0.

An exit code of zero alone does not mean the ticket was merged or even that the
run completed. Check the printed state and `status`. CLI argument parsing can
also return code 2 before a run is created.

## Recovery

For an existing example run id of 1:

```sh
uv run fugue status 1
uv run fugue resume 1
```

Inspect status and the reported problem first, then correct it before resuming.
Use the same repository, configuration and state directory. Resume can reconcile
operations recorded before interruption; deleting local state removes the
information needed for that recovery. It does not create missing human decisions.

For configuration errors, inspect the file with `config show`; correct unknown
keys, malformed TOML, non-positive limits or missing GitHub Project settings.
For provider-limited outcomes, address the provider's sign-in or quota condition
before retrying. For interruption, preserve the worktree and local state.
For a failed gate or CI, inspect the failing output and fix the cause; raising
the repair limit does not repair the failure. When approval is stale, review the
new head and request fresh approval through the [workflow](workflow.md).

These examples describe real workflow commands. Starting, reviewing, resuming,
applying children, approving or merging can make external calls or changes;
help and configuration inspection do not establish that those services are ready.
