# Getting started

Fugue is a local-first CLI that coordinates coding agents from a GitHub ticket to
review, approval and delivery. It is under development; no release is available.
This guide uses a source checkout.

## Prerequisites

- Python 3.12 or newer and uv.
- Git and a local checkout of the repository whose tickets you want to work on.
- The GitHub CLI, authenticated with access to that repository and its GitHub
  Project, including the permissions needed to update issues and pull requests.
- The unmodified Claude and Codex CLIs on your PATH for the default roles.
  Sign in to each provider CLI yourself before starting. Fugue defaults to
  `subscription_login`; `api_key` is an explicit, metered choice.
- A repository using Fugue's ticket structure and Project fields. See the
  [development process](../dev/process.md) and
  [ticket planning](../dev/ticket-planning.md) for that contract. Fugue does not
  provision a GitHub Project or its fields for you.

From the Fugue source checkout, install the existing dependencies and inspect
help:

```sh
uv sync
uv run fugue --help
```

## Configure the target repository

Create `fugue.toml` at the target repository's Git top level. Replace the example
repository, Project owner and Project number with your own values:

```toml
[github]
repo = "OWNER/REPOSITORY"
project_owner = "OWNER"
project_number = 2
```

The repository can instead be inferred from its GitHub `origin` remote; the
Project owner and number are required for workflow commands. The default gate
runs Python tooling, so configure `policy.gate_commands` for the target repository
before a run. See [configuration](configuration.md) for all fields, defaults and
storage locations.

When using Fugue from its source checkout, pass the target repository explicitly:

```sh
uv run fugue config show --repo-root /path/to/repository
uv run fugue run 47 --repo-root /path/to/repository
```

These examples assume ticket 47 exists and is ready for the workflow. `run` can
invoke providers, create a worktree, commit and push changes, and open a pull
request. Read the ticket's scope and effective configuration first. The selected
solution for a Size M or L ticket requires a developer decision.

## Follow the run

The output gives a run id. A ticket number identifies the GitHub issue; a run id
identifies the local execution. For an example run id of 1:

```sh
uv run fugue status 1 --repo-root /path/to/repository
uv run fugue usage --by run --repo-root /path/to/repository
uv run fugue timing 1 --repo-root /path/to/repository
```

Use the same configuration and state directory for later commands. Read the
[workflow guide](workflow.md) for review, decisions, approval, merge and recovery,
and [limitations](limitations.md) for exit codes and failure handling.
