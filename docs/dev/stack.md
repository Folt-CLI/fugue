# Stack

## Confirmed choices

- Python >=3.12 with uv, ruff, mypy strict, pytest and hatchling
  ([#2](https://github.com/Folt-CLI/fugue/issues/2),
  [#5](https://github.com/Folt-CLI/fugue/issues/5)).
- Runtime libraries: typer, rich and pydantic v2. Standard library modules:
  sqlite3, asyncio and tomllib. No ORM, LangGraph, PydanticAI or TUI library
  ([#8](https://github.com/Folt-CLI/fugue/issues/8)).
- Terminal interface: Rich only, with standard-library input (`input`, plus
  `readline` when the platform has it). The terminal layer and chat component
  build on Rich; no prompt or TUI library is added. This replaces the no-TUI
  statement above for terminal output and chat
  ([#95](https://github.com/Folt-CLI/fugue/issues/95)).
- Provider adapters drive the unmodified Claude and Codex command-line tools as
  child processes, with no credential handling and no silent API fallback
  ([#7](https://github.com/Folt-CLI/fugue/issues/7)).
- Package layout: `src/fugue/{config,models,workflow,store,artifacts,process,gitops,github,gate,agents,cli,engine}`
  ([#8](https://github.com/Folt-CLI/fugue/issues/8)).

## Pending decisions

- Provider capability support and authentication mode availability.
- Agent lifecycle, permissions, cancellation and recovery.
- CLI commands and configuration.
- Workflow transitions, role boundaries and human approval defaults.
- Persistence schema, artifact storage, operation records and restart reconciliation.
- GitHub integration, worktrees, stacks and delivery operations.
- Test structure, CI configuration and supported platforms.

Record choices only after a Question selects them, following the
[process](process.md) and its campaign exceptions. Use the terms defined in
[GLOSSARY.md](../../GLOSSARY.md).
