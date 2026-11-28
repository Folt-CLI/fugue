# Principles and architecture

## The goal

Fugue takes a GitHub ticket to a reviewed pull request. The rebuild keeps that goal and changes how the work is done: slowly, one step at a time, with the developer directly influencing each step. The developer's words were: "not tickets, just pure work slowly".

The first target was the smallest end to end: one ticket, one command, `fugue work`. That target has since grown into the hierarchy described in [the work model](work-model.md), and `fugue work` is still a single-ticket command.

## Working conventions (Built)

- The old source was moved to `src-temp/`, which is git-ignored. It is mined for ideas and never imported.
- New code lives in `src/fugue/`. The branch is `refactor`, pushed to `origin`.
- The version is `0.4.0`.
- Commit subjects are `Maintenance[#0]: <subject>`. The body has a **Summary** and, when it touches more than one thing, **Changes**. The branch base still carries the old ticket number (#242), and `#0` marks commits that belong to no ticket.
- Tests are not a focus for now. The developer said so more than once. `tests/` is broken against the new code and is ignored. Only `tests/test_process.py` passes.
- The real GitHub is used for testing. The repository `fugue-tester` is a clone of fugue on `develop`, used to test the pre-refactor behaviour and for trying commands.
- A deterministic regression suite of 44 commands, with a local stub of the GitHub API for writes, compares output against a baseline. It lives in the session scratchpad and is not part of the repository.

## Architecture rules the developer set (Built)

### Core holds all the logic

`src/fugue/core/` contains the whole workflow: the flows, the GitHub layer, the git operations, the process runner, the agents and the gate. It is not a thin wrapper and not a facade class. The developer's words: the core is "the pure logic of the Workflow, and handling of exceptions".

Core never prints and never prompts. It raises exceptions and sends events.

### The command line is an interface

`src/fugue/cli/` only prints what it is told, handles raised `FugueError`s in one place, and starts a core flow. It holds no logic. The design must allow a backend or an MCP side later. That is not planned, but nothing may prevent it.

### The reporter is the seam

A flow takes a `Reporter` with two methods: `report(event)` and `ask(question)`. Events and questions are Pydantic models, so they can be sent as JSON. The command line supplies a Rich reporter for plain output and the terminal screen supplies a Textual reporter. A silent reporter answers every question with its default. Agent output is reported live and can be suppressed with `--quiet`.

### Classes and Pydantic

Classes over module-level functions and globals. Pydantic for all models and for settings, with one `Config` base class. The developer did not like a module-level `_ticket` helper and asked for the object-oriented approach.

### GitHub is one easy class

`Github` has areas: `github.issue.get(...)`, `github.pr`, `github.label`, `github.branch`, `github.repo`, `github.project`. It is meant to be easy to use. See [the GitHub layer](github-layer.md).

### Libraries are looked up, never recalled

The developer's rule, stated twice: "ALWAYS when we talk about libraries PLEASE LOOK UP the latest versions". Every library choice is checked on PyPI and on the web before it is recommended. PyPI is the authority for versions, because search snippets can be stale.

## Libraries and pins (Built)

| Purpose | Library | Version at the time | Notes |
| --- | --- | --- | --- |
| Command line | Typer | 0.29.3 | The developer asked for a real CLI library over argparse. |
| Terminal output | Rich | 15.0.0 | Rich output was a key requirement for the `github` commands. |
| Terminal screen | Textual | 8.2.8 | Chosen when the developer decided to build the TUI now. |
| Models and settings | pydantic, pydantic-settings | 2.14.4 and 2.16.3 | |
| GitHub | githubkit | 0.17.1 | Typed REST. Projects are read as raw JSON because githubkit rejects the multi-select field type. |
| Git | pygit2 | 1.21.0 | Worktrees and fetch without the git CLI. |
| Flow graph | pydantic-graph | git commit `1cbd8fa8` | Class nodes. No persistence in this build. |
| Agent SDK (Claude) | claude-agent-sdk | 0.2.189 | Not used in code yet. |
| Agent SDK (Codex) | openai-codex | 0.172.0 | Not used in code yet. |
| TOML writing | tomlkit | 0.15.3 | Keeps comments when `fugue config set` writes. |

`pydantic-ai-slim` is pinned to the same git commit as pydantic-graph (the branch with the `claude-agent:` model). It is still a dependency, but see [Agents](agents.md): through that model, Claude Code's own tools are switched off, so agents are to run through the SDKs directly.

Git and GitHub use SDKs and not the command line tools. The developer's words: "i want GIT and GITHUB to use SDKs instead of CLIs commands". The agents are also to run through their SDKs, "OpenAI Codex SDK, and Claude Agent SDK, they allow the use of both subscriptions". Both were smoke tested on subscription logins. Anthropic's terms for subscription use through the SDK are **Open**: unverified.

The token for GitHub comes from the `GITHUB_TOKEN` environment variable. The developer does not want to depend on the `gh` command. The token needs the `project` scope (or `read:project` to read) for project fields. GitHub answers 404 and not 403 when the scope is missing, and Fugue names the missing scope in its error.

## Process runner (Built)

`core/process/runner.py` was copied as it was, because it is tested. It runs a command with bounded capture, kills the whole process group, and streams lines of stdout and, since the gate, stderr to callbacks.

## The package map (Built)

```text
src/fugue/
  cli/            the interface: Typer commands, Rich views, the Textual screen
  core/
    agents/       prompts, templates and the tools agents use to ask
    flow/         the Flow and Step base classes, actions, checkpoint, run records
    github/       the Github class and its areas
    gitops/       the repository and worktrees, with pygit2
    process/      the async process runner
    reporting/    events, questions, the Reporter
    work/         the work flow
    settings.py   what `fugue config` reads and writes
  models/         frozen Pydantic models shared everywhere
  prompts/        the agents' prompts and the templates they follow
  config.py       the settings classes
  configfiles.py  the settings files
  errors.py       FugueError and its subclasses
```
