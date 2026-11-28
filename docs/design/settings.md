# Settings

## Layers

**Built.** Settings come from files in layers. Each layer overrides the one before it. The developer suggested the structure ("a .fugue/ directory is better with a config.toml file, and we can do fugue config local/global") and approved it with two tweaks, which are built.

From the weakest to the strongest:

1. The default in the code.
2. **Global**: `~/.config/fugue/config.toml` (or `$XDG_CONFIG_HOME/fugue/config.toml`), for every repository on the machine.
3. **Local**: `.fugue/config.toml` in the repository. It is meant to be committed and shared.
4. **Private**: `.fugue/config.local.toml` in the repository, the developer's alone, git-ignored.
5. **Environment variables.**
6. **Command-line flags.**

The repository is found by walking up from the current directory to the first folder with a `.git`. A settings file is read only when it exists.

`.fugue/runs/` (the run records) and `.fugue/config.local.toml` are git-ignored, and `.fugue/config.toml` is not, so a shared setting such as the project number can be committed.

The old `fugue.toml` is no longer read. The developer: "REMOVE THIS NO NEED TO BE BACKWARDS COMPATIBLE".

## Rules

- **Local-only settings** belong to one repository and are never taken from the global file, and `fugue config set --global` refuses them. The developer: "certain things CANT be set to the global one, i do not want a global setting for github.project since its local only." Today these are `github.project` and `work.base_branch`.
- **Secrets are never read from a file**, and cannot be written to one. The GitHub token is an environment variable only.
- A value is checked against the setting's type when it is written. A table, such as the gate, is given as JSON.
- Writing keeps the rest of the file, comments included.
- An empty environment variable counts as unset.

## `fugue config`

- `fugue config list`: every setting, its value, and which file or variable it comes from, then the settings files and whether each exists. A local-only setting found in the global file is flagged as having no effect.
- `fugue config get KEY`: the value alone, for scripts. Exit code 1 when it is not set.
- `fugue config set KEY VALUE`: written to `.fugue/config.toml` by default, `--private` for the private file, `--global` for the global file. A `--private` write warns when git is not ignoring the file.
- `fugue config unset KEY`: removes it from a file so the next layer down applies.

## Every setting

| Setting | Environment variable | Default | Notes |
| --- | --- | --- | --- |
| `github.token` | `GITHUB_TOKEN` | required | Environment only. Needs the `project` scope for project fields. |
| `github.api_url` | `GITHUB_API_URL` | `https://api.github.com` | Another API address, such as the local stub. |
| `github.project` | `GITHUB_PROJECT` | none | The project number for ticket fields and status. Local-only. Also `fugue work --project`. |
| `work.todo_status` | `FUGUE_WORK_TODO_STATUS` | `S0: To Do` | The only status a ticket may have when work starts on it. |
| `work.analysis_status` | `FUGUE_WORK_ANALYSIS_STATUS` | `S1: In Analysis` | The status while the plan is made. |
| `work.ready_status` | `FUGUE_WORK_READY_STATUS` | `S2: Ready for Implementation` | The status once the plan is written. |
| `work.started_status` | `FUGUE_WORK_STARTED_STATUS` | `S3: In Implementation` | The status while the unit is implemented. |
| `work.review_status` | `FUGUE_WORK_REVIEW_STATUS` | `S4: In Review` | The status while the unit is reviewed. |
| `work.status_field` | `FUGUE_WORK_STATUS_FIELD` | `Status` | The project field that holds the status. |
| `work.worktree_root` | `FUGUE_WORK_WORKTREE_ROOT` | `.worktrees` in the repository | One folder per branch under it. |
| `work.base_branch` | `FUGUE_WORK_BASE_BRANCH` | the repository's default | Local-only. |
| `work.slug_length` | `FUGUE_WORK_SLUG_LENGTH` | 40 | The longest slug in a branch name. |
| `work.gate` | `FUGUE_WORK_GATE` | none | A table of check name to command, run in order. |
| `work.gate_timeout` | `FUGUE_WORK_GATE_TIMEOUT` | 1800 | Seconds one check may run. |
| `work.auto` | `FUGUE_WORK_AUTO` | false | Answer the permission questions. Also `--auto`. |
| `work.record_runs` | `FUGUE_WORK_RECORD_RUNS` | true | Write the run's events to `.fugue/runs/`. |
| `work.runs_dir` | `FUGUE_WORK_RUNS_DIR` | `.fugue/runs` | Where the records go. |
| `agents.interview` | `FUGUE_AGENTS_INTERVIEW` | none | Path of extra guidance for the interview agent. |
| `agents.logic` | `FUGUE_AGENTS_LOGIC` | none | The same for the logic agent. |
| `agents.plan` | `FUGUE_AGENTS_PLAN` | none | The same for the plan agent. |
| `agents.implementation` | `FUGUE_AGENTS_IMPLEMENTATION` | none | The same for the implementation agent. |
| `agents.reviewer` | `FUGUE_AGENTS_REVIEWER` | none | The same for the reviewer. |

Other options are flags only: `fugue work --headless` (also `FUGUE_HEADLESS`), `--quiet`.

## Not decided

- **Open.** Which agent and which model serves each role, and the model tier for each ticket size. There is no setting for it yet. The developer said to stay on mid tiers for now.
- **Open.** The limits: questions an interview may ask, repair rounds of the gate, and the safety cap on review rounds, which the developer said has no strict limit.
- **Open.** The Backlog milestone's name and the Priority value used for follow-ups are fixed in the design but are not settings.

## Where the project is set, in practice

`GITHUB_PROJECT` was awkward to export for each workflow. The answer is the file layer: `fugue config set github.project 2` in the repository writes `.fugue/config.toml`, so nothing has to be exported. In this repository it is set to 2, the Folt-CLI project.

In this repository the local file sets `github.project` to 2 and a gate of four checks (the formatter, the linter, the type checker on `src`, and the build). The tests are left out of that gate because the old tests do not run against the new code. The file is not committed yet.
