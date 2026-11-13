# Fugue

A local-first CLI that coordinates coding agents through a structured ticket-to-delivery workflow. Like independent voices in a fugue, agents contribute distinct work within a shared structure.

Current release: **0.1.0**.

## Usage

Download `fugue-0.1.0-py3-none-any.whl` from the
[0.1.0 release](https://github.com/Folt-CLI/fugue/releases/tag/v0.1.0)
and install it with Python 3.12 or newer and uv:

```sh
uv tool install /path/to/fugue-0.1.0-py3-none-any.whl
```

Inspect the installed CLI and effective configuration:

```sh
fugue --help
fugue config show
```

After configuring the target repository and signing in to the required CLIs,
start a ticket and inspect the returned run id:

```sh
fugue run 47 --repo-root /path/to/repository
fugue status 1 --repo-root /path/to/repository
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
