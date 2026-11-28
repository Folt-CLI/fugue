# The GitHub layer

## The class

**Built.** `Github()` reads the repository from the `origin` remote of the current directory and the token from `GITHUB_TOKEN`. It exposes areas, so that `github.issue.get(246)` is all it takes. The developer asked for it to be "EASY to use".

| Area | What it does |
| --- | --- |
| `github.issue` | get, list, create, edit, comment, comments, close (with not planned), reopen |
| `github.pr` | get, list, resolve a pull request from head and base, create (with draft), edit, close, reopen, comment, comments, merge, checks |
| `github.label` | list, create and the other label operations |
| `github.branch` | list, delete, default |
| `github.repo` | information about the repository |
| `github.project` | list, get, fields, overview, items, table, item, details, add, set, clear, remove |

The same operations are available as `fugue github issue|pr|label|branch|repo|project ...`, thin commands with Rich output. They exist for the developer's own use: "an abstraction of the Github class to turn into commands". The developer asked for everything that might be needed: "create everything we might need for github".

A failed request becomes a `GitHubError` with a message that names the subject. When the token is a classic one without the project scopes, GitHub answers 404, and Fugue says which scope is missing.

## Projects

Projects are "the main use case, they are the focus along with issues". They are read as raw JSON from `/orgs/{org}/projectsV2/...`, because githubkit does not accept the multi-select field type. Writes go through the same route. A field is read by name and set by option name, and `set` returns the field's real name and new value.

The real project is number 2 of the `Folt-CLI` organisation. Its fields, as read: Assignees, Status, Milestone, Repository, Type, Reviewers, Parent issue, Sub-issues progress, Priority, Start date, Target date, Size, Component and Disposition.

The ticket window shows these in a fixed order: Status, Size, Type, Priority, Component, Disposition, Milestone, Assignees, then the rest.

The project's Status values are `S0: To Do`, `S1: In Analysis`, `S3: Ready For Implementation`, `S4: In Implementation` and `S5: Done`.

## Issue types

The organisation has nine issue types, which the tiers use in full: Task (a specific piece of work), Bug, Feature, Question (investigation), Epic, Maintenance, Documentation, Incident (something wrong found outside the current change) and Test.

## What is missing

**Open.** For the new flows the layer needs:

- Create an issue with a **type**, a **milestone** and project fields (Priority, Size, Status).
- Read a ticket's **parent**, its **sub-issues** and its **blocked-by** links, to enforce "a unit must have an M parent" and to order units. githubkit 0.17.1 has `get_parent`, `list_sub_issues`, `add_sub_issue` and `add_blocked_by_dependency`.
- **Search** open tickets, to find earlier logic to build on and to dedupe backlog follow-ups.
- **Apply an analysis** to a ticket: edit the body sections and attach the logic file, then refetch. Attachments in the old process were uploaded with the `gh-attach` extension through a signed-in browser session. How Fugue attaches a file without `gh` is not settled.
- Open a draft pull request, and mark it ready.

The `Ticket` model has no parent, children or milestone yet.

## Unverified

- Writes against the real GitHub were tested only through a local stub. Reads and the project status moves were tested against the real one.
- The real response shape of creating a label.
- Projects owned by a user and not an organisation.
- Fetching over an SSH remote. Fetching over HTTPS uses the token.
- Where `GITHUB_TOKEN` is exported in the developer's shell. It was found to have no `project` scope while the saved `gh` login did, and the source of the variable was not found.
