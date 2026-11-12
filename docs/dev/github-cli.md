# GitHub and `gh` reference

For developer decisions, questions and merge authorization below, ordinary repository rules apply; see [Autonomous development campaign exceptions](process.md#autonomous-development-campaign-exceptions) for the campaign.

Use `gh` for repeatable issue and pull request operations. Pass the repository
explicitly on commands that accept the repository flag:

```sh
--repo Folt-CLI/fugue
```

The GitHub issue body is the durable record. Before editing it, read the current
issue; when replacing the body, provide the complete updated text so existing
sections are preserved.

Use `Folt-CLI/fugue` in every repository argument and maintained GitHub URL.
Stack commands use the current checkout and accept no repository flag.
Attachment downloads use the returned URL; API operations use the explicit
repository endpoint rather than a repository flag. The initial local origin is
`https://github.com/Folt-CLI/fugue.git`; inspect `git remote -v`
before changing any remote configuration.

The Fugue Project is [Folt-CLI Project #2](https://github.com/orgs/Folt-CLI/projects/2).
When the environment token lacks Projects scopes, use
`env -u GITHUB_TOKEN -u GH_TOKEN` before the Project operation so the stored
account credentials can be used. Missing credentials remain a blocker.

## Issues

```sh
# List open issues
gh issue list --repo Folt-CLI/fugue

# Read the issue before changing it
gh issue view 2 --repo Folt-CLI/fugue

# Create from a prepared issue body
gh issue create \
  --repo Folt-CLI/fugue \
  --title "<focused outcome>" \
  --body-file /path/to/issue.md \
  --parent 1

# Replace title/body
gh issue edit 2 \
  --repo Folt-CLI/fugue \
  --title "<updated title>" \
  --body-file /path/to/complete-updated-issue.md

# Add a short decision or handoff
gh issue comment 2 \
  --repo Folt-CLI/fugue \
  --body "<short update and links>"

# Close after the work is completed and verification is recorded in the PR
gh issue close 2 --repo Folt-CLI/fugue
```

Use the repository's GitHub issue forms when creating a work item, epic, or
incident in the web interface. `gh issue create` accepts a title/body but does
not fill out the form interactively; use a prepared Markdown body that follows
[issue-format.md](issue-format.md).

Use the parent specified by the developer or an existing delivery group or
Question in [workflow](workflow.md#dependencies-and-grouped-work); independent
release work defaults to #1. Choose the issue type appropriate to the request.
Verify the native parent and issue type after creation with
`gh issue view <number> --repo Folt-CLI/fugue --json parent,issueType`.
Do not add a parent reference to the body. Set assignment, milestone and project
membership following [ticket planning](ticket-planning.md#at-issue-creation).

## Markdown attachments

The built-in `--attach` flag supports images and videos. For Markdown documents,
use the [sudosubin/gh-attach](https://github.com/sudosubin/gh-attach) extension.
Install that extension once before uploading.

Document uploads require a signed-in GitHub browser session matching the `gh`
account. The extension uses that session automatically. Do not copy session
cookies into commands, logs, or issue text.

```sh
gh attach /tmp/2-code-findings.md /tmp/2-solution-1.md /tmp/2-solution-2.md \
  --repo Folt-CLI/fugue --json href,name
```

Use the returned `href` values as normal Markdown links in the issue body.
Read the latest issue body first and preserve other sections. Verify every
uploaded file by downloading it and comparing it with the original:

```sh
gh attach download '<returned-url>' --output /tmp/downloaded-solution.md
cmp /tmp/2-solution-1.md /tmp/downloaded-solution.md
```

Keep uploads linked from the issue so reviewers can access them. Do not mark a
solution selected until the developer chooses it.

## Issue types and fields

Set the issue type by PATCHing `repos/Folt-CLI/fugue/issues/<n>` with
`type` set to the exact issue type. Read issue field values from
`repos/Folt-CLI/fugue/issues/<n>/issue-field-values`; POST to that endpoint
with a JSON input file to set values. Use the GitHub REST API for these operations.

The input has this shape:

```json
{"issue_field_values":[{"field_id":49325272,"value":"High"}]}
```

Priority has field id `49325272` and values Urgent, High, Medium and Low.
Component has field id `49332254` and takes an array of Build, Dev Docs,
Dev Tools, Source Code, Tests and User Docs, for example:

```json
{"issue_field_values":[{"field_id":49332254,"value":["Dev Docs"]}]}
```

Disposition has field id `49332255` and values Abandoned, Answered, Duplicate,
Invalid, Merged, Obsolete, Out of Scope, Released, Superseded, Withdrawn and
Won't Do. Set Component after solution selection and Disposition when ready to
close. Follow [ticket planning](ticket-planning.md#fields-after-decisions) and
[working templates](working/README.md) for timing and limits. Read the issue
first and preserve unrelated metadata when making changes.

## Pull requests

```sh
# Open a PR and link the issue so GitHub can close it on merge
gh pr create \
  --repo Folt-CLI/fugue \
  --title "<change>" \
  --body-file /path/to/pr-description.md

# Inspect review state before merge
gh pr view <number> --repo Folt-CLI/fugue
```

The PR body should include `Closes #<issue-number>` when merging it completes
the linked work item. Follow the
[parent closing rules](working/README.md#shared-steps) before using a closing
keyword for a parent. For ordinary use, a merge is a human review decision; see the campaign exceptions. Do not merge
unless the repository's required checks and review are satisfied.

### Inspect checks

Follow [Testing](testing.md#ci-result-in-the-review) for CI analysis and the
review handoff:

```sh
gh pr checks <number> --repo Folt-CLI/fugue --watch
gh run view <run-id> --repo Folt-CLI/fugue --log-failed
```

### Posting a code review

Post a review through the GitHub REST API with a JSON input file. Set `commit_id` to the head
commit being reviewed, `event` to `COMMENT`, and include a summary `body`. For
each inline comment, provide the changed `path`, `line`, `side` and comment
`body`:

POST `review.json` to `repos/Folt-CLI/fugue/pulls/{n}/reviews`.

See [Review](review.md) for the review payload format and review cycle. Resolve
fixed threads through GraphQL `resolveReviewThread`; reply with the reason when
a finding is rejected.

### Stacked pull requests

Use `gh stack` from the [stacked pull request guide](stacked-prs.md). These
commands require the `github/gh-stack` extension:

| Command | Purpose |
| --- | --- |
| `gh stack init --base develop <branch>...` | Start a stack, optionally adopting branches bottom to top |
| `gh stack add <branch>` | Add a branch above the current stack position |
| `gh stack link <branch-or-pr>...` | Link existing branches or pull requests bottom to top |
| `gh stack view` | View stack order and pull request status |
| `gh stack rebase` | Cascade-rebase stack branches |
| `gh stack sync` | Fetch, rebase, push and sync remote stack state |
| `gh stack push` | Push stack branches with per-branch `--force-with-lease` |
| `gh stack submit` | Push branches and create or update pull requests |
| `gh stack merge` | Merge a stack; developer only |
