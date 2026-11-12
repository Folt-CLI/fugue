# GitHub setup

Created and inspected on 2026-11-12.

## Resources

- Repository: https://github.com/Folt-CLI/fugue (public, matching Folt visibility).
- Fugue Project: https://github.com/orgs/Folt-CLI/projects/2 (private, matching Folt Project visibility).
- Source Project: https://github.com/orgs/Folt-CLI/projects/1.
- Fugue Project node ID: `PVT_kwDOFO3Uoc4BrXFq`.
- Repository node ID: `R_kgDOVsUL-Q`.
- Release Epic: https://github.com/Folt-CLI/fugue/issues/1, `0.1.0 -- Fugue`.
- Milestones: `0.1.0` and `Backlog`.

The source Project was copied through GitHub's native project-copy operation without its tickets or draft issues. Fugue's repository was linked explicitly. No Folt work items or source configuration were changed.

## Verified Project parity

Read-only GraphQL comparison confirmed equality of:

- Visibility, closed state, description, and README.
- All 19 fields, field types, available options, colors, and descriptions exposed by the API.
- Both views, their layouts and filters, visible columns, grouping, vertical grouping, and sorting.
- Enabled state of all six copied built-in workflows.

Views:

- All Open Items: table, `is:open sub-issues.is:open`.
- All Items: table, no filter.

Copied workflows:

| Workflow | Enabled |
| --- | --- |
| Auto-add sub-issues to project | Yes |
| Auto-close issue | No |
| Item added to project | Yes |
| Item closed | Yes |
| Pull request linked to issue | Yes |
| Pull request merged | Yes |

GitHub's native copy preserves workflow configuration, except auto-add workflows. The public API verified workflow names/enabled states; it does not expose their complete trigger/action configuration. No new workflow actions were invented.

## Remaining parity gap: repository auto-add

Folt has an enabled `Auto-add to project` workflow. GitHub explicitly excludes this workflow from Project copies, and the available public GraphQL API has no create/update workflow operation to configure it. No authenticated browser tool was available during setup.

To finish exact functional parity, use GitHub's Project UI:

1. Inspect Folt Project #1's auto-add filter without changing it.
2. Open Fugue Project #2, menu, Workflows, Auto-add to project.
3. Select `Folt-CLI/fugue`, use the equivalent filter, and choose Save and turn on workflow.
4. Verify a subsequently created or updated matching Fugue item appears in Project #2.

Until configured, explicitly add every Fugue issue and PR to Project #2. This keeps work tracked but does not mean the native auto-add rule exists. Do not claim exact parity until the rule is configured and verified.

References:

- https://docs.github.com/en/issues/planning-and-tracking-with-projects/creating-projects/copying-an-existing-project
- https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/adding-items-automatically

## Fields and issue types

Fugue inherits the nine enabled Folt-CLI issue types: Task, Bug, Feature, Question, Epic, Maintenance, Documentation, Incident, Test.

Shared organization issue-field IDs:

- Priority: `49325272`.
- Component: `49332254`.
- Disposition: `49332255`.

Do not create duplicate organization fields or mutate their options. Use native issue-field APIs for shared issue values.

Project-specific fields:

- Status: `PVTSSF_lADOFO3Uoc4BrXFqzhpqKC8`.
- Size: `PVTSSF_lADOFO3Uoc4BrXFqzhpqKFo`.

Status options: S0: To Do, S1: In Analysis, S3: Ready For Implementation, S4: In Implementation, S5: Done. Sizes: XS, S, M, L, XL.

Refresh IDs/options before future mutations instead of assuming they never change. The live source Project has no Estimate or Iteration field.

## Repository setup scope

Issues and Projects are enabled, Wiki disabled, Discussions enabled. Merge commits, squash merges, and rebase merges are allowed; auto-merge and automatic deletion of merged branches are disabled, matching Folt's repository-level toggles.

Folt's Rust CI, licensing choices, security policy, topics, and branch rules were not blindly copied. Establish Fugue-specific process templates, checks, and integration branch protection through the campaign bootstrap tickets. A required CI gate must correspond to a real workflow before protection is enabled.

The initial documentation commit establishes main and develop, with develop as the default branch. No application implementation or full campaign backlog was created by setup; only the release grouping Epic exists.

## Local CLI authentication note

The process environment's active GitHub token can access repositories but lacks Projects scopes. The saved `gh` account can access Projects when `GITHUB_TOKEN` and `GH_TOKEN` are unset for that command. Use the appropriate supported `gh` account context; never print tokens or copy them into files.
