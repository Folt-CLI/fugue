# Fugue

A local-first CLI that coordinates coding agents through a structured ticket-to-delivery workflow. Like independent voices in a fugue, agents contribute distinct work within a shared structure.

Status: Under development; no release is available.

## Usage

From this source checkout, inspect the CLI and effective configuration:

```sh
uv run fugue --help
uv run fugue config show
```

After configuring the target repository and signing in to the required CLIs,
start a ticket and inspect the returned run id:

```sh
uv run fugue run 47 --repo-root /path/to/repository
uv run fugue status 1 --repo-root /path/to/repository
```

Replace ticket 47, run id 1 and the path with your own values. A run can invoke
providers and create Git and GitHub changes. Human solution selection and merge
approval are the defaults.

- [Getting started](docs/user/getting-started.md): prerequisites and first run.
- [Configuration](docs/user/configuration.md): TOML fields, defaults and authentication.
- [Workflow](docs/user/workflow.md): selection, review, approval, merge and recovery.
- [Limitations](docs/user/limitations.md): boundaries, failure handling and exit codes.
- [Glossary](GLOSSARY.md): product and process terms.

## Development

The gate consists of these five commands:

```sh
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv build
```
