# Pull request review

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](process.md#autonomous-development-campaign-exceptions) for the campaign.

After a pull request is opened or updated, where the [size requires review](working/README.md),
run a read-only review of its
diff against the pull request's base. For a stack, review each pull request
against its own base. CI and branch protection on the bottom base apply to
every layer; see [Stacked pull requests](stacked-prs.md).

## Review contract

The reviewer judges the diff against the ticket scope, the scope's In-scope
scenarios and repository policy. It reports two lists, Blocking and
Non-blocking.

- **Blocking:** a defect in code changed by the diff with a concrete trigger
  reachable in a listed In-scope scenario or in ordinary use of the feature, a
  wrong result that matters (data loss, security hole, incorrect output or a
  crash in normal operation) and reproduction steps with inputs. At most three
  per round, ranked.
- **Non-blocking:** hardening, rare or pathological timings, speculative misuse
  and style. At most five, one line each; they never block a merge. The
  orchestrator collects them into one follow-up ticket per pull request when
  worth keeping.
- A finding about a scenario under Out-of-scope scenarios is Non-blocking by
  definition. A finding about an unlisted scenario is Blocking only when it
  meets the criteria above for ordinary use.
- `No findings.` is the correct answer when nothing is blocking.
- Passes: correctness against the scope always; restart and failure safety only
  when the scope lists restart or failure scenarios; security only when the
  scope touches subprocesses, the filesystem, credentials or the network.
- Merge when a round has no Blocking findings. There is no limit on the number of review rounds; the
  criteria narrow what can block. The orchestrator adjudicates disputes and
  records the reason. Fixes must not add persisted state or new behaviour that
  no listed scenario needs.

See [Issue format](issue-format.md#implementation-scope-layout) for the scenario sections.

### Reviews live on GitHub

GitHub code review is a mandatory part of the contract, not an optional step.

- Every review round is posted to the pull request as one GitHub code review
  (event `COMMENT`, `commit_id` set to the exact head reviewed), including a
  clean round, whose body states `No findings.`. Findings are never kept only in
  terminal output or in a ticket comment.
- Each finding is an inline comment on its changed line (`path`, `line`,
  `side`), so each finding is its own thread. A finding with no changed line goes
  in the review body. The reviewer therefore gives `file:line` for every finding
  that has a location.
- After an agent fixes an accepted finding, the gate passes and the fix is
  pushed, the orchestrator replies on the finding's thread with
  `Fixed in <sha>: <what changed>` and resolves the thread through GraphQL
  `resolveReviewThread`. The implementing agent does not use `gh`; the
  orchestrator does this, so no fixed finding leaves an open thread.
- A rejected finding gets a reply with the reason, and the thread is resolved. A
  deferred Non-blocking finding gets a reply `Deferred to #N` naming the
  follow-up ticket, and the thread is resolved.
- The pull request therefore shows the full history: findings per round, fixes
  with commit SHAs, and declined items with reasons. Unresolved threads block the
  merge guard.

See [Post findings](#post-findings) for the procedure.

## Review agent

Run headless Codex with `codex exec`. The `-p` option means `--profile`; it does
not supply the prompt. `codex exec review --uncommitted` does not accept a
custom prompt. For a custom prompt, run:

```sh
codex exec -s read-only -C <worktree> -o <file> - < prompt
```

The prompt should tell the agent to review read-only, compare the PR diff with
its base, and apply the [Review contract](#review-contract). Include the ticket
scope with its In-scope and Out-of-scope scenarios, and the rounds log of
earlier accepted, rejected and deferred findings. Do not ask for new tests or dependencies; do review the tests
in the diff. Return Blocking and Non-blocking lists. Each Blocking finding gives
what goes wrong, the trigger, reproduction steps with inputs, the fix when clear,
and `file:line`; each Non-blocking finding is one line. Keep findings tied to the
changed diff where possible.

The read-only sandbox has no network or `gh` access and runs no builds or checks. The orchestrator posts reviews and runs the gate in
[Testing](testing.md#gate-before-push). Implementation runs use
`-s workspace-write` in the issue's worktree and leave an uncommitted diff for
the orchestrator to review and deliver through [Workflow](workflow.md#stages).

## CI result

Follow [Testing](testing.md#ci-result-in-the-review): the orchestrator fetches
and analyses CI, includes the result and failing excerpts under **CI result**
in the review prompt, and waits for CI to finish before posting the review.

## Dependabot PRs

Always run a read-only review on every Dependabot PR. Verify that the bump
will not break workflows or code using the diff, CI result and release notes of
the bumped dependencies. The reviewer has no network; paste the CI result and release
notes into the review prompt. When clean, leave a PR comment whose entire body
is exactly `No issues.`. Otherwise post findings as for any review below.

The commit subject is `Dependabot[#0]: <Dependabot's description>`, set through
`commit-message.prefix` in `.github/dependabot.yml`.
`Dependabot` is the only subject type that is not an issue type; `#0` means
there is no ticket and none is needed, including an epic or issue. If the PR's
commits do not follow this format, squash them into one commit with that subject.

## Post findings

The orchestrator posts every review round, including a clean one, as one GitHub code review using
a REST POST to `repos/Folt-CLI/fugue/pulls/{n}/reviews` with a JSON input file. Set `commit_id`
to the reviewed head commit and `event` to `COMMENT`. Agents act as the PR
author and cannot approve their own pull request. Include a summary body with
finding counts and categories with no findings, or `No findings.` for a clean round. Add one inline comment per
finding on a changed line, with `path`, `line` and `side`. Put findings in the
diff that cannot be attached to a changed line in the summary. The orchestrator
records findings about something outside the pull request's diff as incidents
using the **Incident report** form (see [Incident reports](incidents.md)),
links them from the review summary, and leaves them out of the inline comments.
Findings about changed lines stay in the pull request.

For example, `review.json` may contain:

```json
{
  "commit_id": "0123456789abcdef0123456789abcdef01234567",
  "event": "COMMENT",
  "body": "Review: 1 High, 0 Medium, 0 Low. No findings in performance or security.",
  "comments": [
    {
      "path": "src/fugue/example.py",
      "line": 42,
      "side": "RIGHT",
      "body": "High: This can raise an exception when the input is empty. Trigger: pass an empty list. Return an error instead."
    }
  ]
}
```

Use `gh pr review --repo Folt-CLI/fugue` for body-only reviews. Record requested changes on the
ticket. An implementation agent fixes accepted findings on the same branch as
further commits, and the review cycle repeats until a round has no Blocking findings.
Once a fix is pushed and the gate has passed, the orchestrator replies
`Fixed in <sha>: <what changed>` on the finding's thread and resolves it through
GraphQL `resolveReviewThread`. If the orchestrator rejects a finding, reply on
its thread with the reason, resolve the thread and do not fix it. Reply
`Deferred to #N` and resolve the thread for a deferred Non-blocking finding. Give the review agent the accepted, rejected and deferred findings
and their reasons as context for the next round, such as in the prompt. It must
not report them again; a rejected finding returns only when the agent can show
that the reason does not hold.

## Unverified

The exact `line` and `side` parameters for inline review comments, whether an
agent-author can request changes on its own pull request, and retargeting after
a merge-commit merge have not been verified. Use `COMMENT` for agent reviews.
