# Configuration

Fugue reads TOML validated by
[`src/fugue/config/schema.py`](../../src/fugue/config/schema.py). Unknown keys
are rejected. A missing configuration file uses defaults; an explicitly requested
file that cannot be read, malformed TOML or invalid values produces an error.

## Discovery and inspection

Commands accept `--config` and `--repo-root` after the command name. Without
`--repo-root`, the repository root is the Git top level of the current directory.
Fugue reads `fugue.toml` there if present. `--config` chooses a file instead;
there is no layered merge of multiple files. Omitted fields retain their defaults.

```sh
uv run fugue config show
uv run fugue config show --config /path/to/fugue.toml --repo-root /path/to/repository
```

`config show` prints the effective configuration as TOML, omitting unset optional
values. It can inspect defaults outside a Git repository. It does not verify
provider sign-in, GitHub access or Project setup.

## Example

This example sets the GitHub Project and makes the default roles and authentication
mode explicit. Replace the GitHub values before use.

```toml
[github]
repo = "OWNER/REPOSITORY"
project_owner = "OWNER"
project_number = 2

[roles.analyst]
provider = "claude"
authentication_mode = "subscription_login"

[roles.implementer]
provider = "codex"
authentication_mode = "subscription_login"

[roles.reviewer]
provider = "codex"
authentication_mode = "subscription_login"
```

## Roles and authentication

Each role (`analyst`, `implementer`, `reviewer`) accepts:

| Field | Values and default |
| --- | --- |
| `provider` | `claude` or `codex`; defaults as in the example |
| `model` | Optional string; unset by default |
| `authentication_mode` | `subscription_login` by default; the schema also accepts `api_key`, but both bundled adapters reject it when invoked |

Omitted roles keep their defaults. If you supply a role table, include its
`provider`. Both bundled provider adapters currently require `subscription_login`,
using your own sign-in to the unmodified provider CLI. The schema accepts `api_key`
when explicitly set in TOML, but both adapters reject it when invoked. Credentials
belong in the provider's supported environment or sign-in mechanism, not in this
file. There is no automatic fallback to metered authentication.

### Provider fallback

A role may list further providers, tried in order when the role's own provider is
unavailable (rate or usage limited, not signed in, or unreachable):

```toml
[roles.implementer]
provider = "codex"

[[roles.implementer.fallback]]
provider = "claude"
model = "example-model"
```

Each `[[roles.<role>.fallback]]` entry accepts `provider` (`claude` or `codex`),
an optional `model` and `authentication_mode`. A provider must not repeat within
one role, and an entry never accepts `api_key`: fallback never uses metered
authentication. The whole call moves to the next provider; a provider is skipped
without a call when it lacks a capability the call needs.

After a provider is unavailable it is skipped for `limits.provider_cooldown_seconds`
(default 300, must be positive) while a later provider in the chain can serve the
call. When every provider is in cool-down they are tried in order anyway. The
cool-down is kept in memory for the current process only.

`fugue overview` reads at most `limits.overview_max_tickets` open tickets
(default 100, must be positive) and reuses a cached analyst summary for
`limits.overview_cache_seconds` (default 300, must be positive) while the
gathered facts are unchanged.

## GitHub

| Field under `[github]` | Default and meaning |
| --- | --- |
| `repo` | Unset; infer `OWNER/REPOSITORY` from the GitHub `origin` remote |
| `project_owner` | Unset; required for workflow commands |
| `project_number` | Unset; positive integer, required for workflow commands |
| `reviewer_identity_separate` | `false`; single-account reviews use COMMENT because GitHub forbids author self-approval |
| `resolve_token_env` | Unset; optional name of the environment variable holding a token for resolving review threads; otherwise use the normal environment |

Use `fugue reviewer-app setup` to register a private reviewer app; see
[reviewer identity setup](reviewer-identity.md) for permissions, key storage and
`reviewer_identity_separate`. Fugue does not yet post reviews as the reviewer app.

`resolve_token_env` is a variable name, not the token itself. Its value must be
non-empty after whitespace is stripped.

## Paths and local state

These optional top-level keys must appear before any TOML table header:

| Field | Default and meaning |
| --- | --- |
| `worktrees_root` | A sibling directory of the repository, named `<repository-name>-worktrees` |
| `state_dir` | `$XDG_STATE_HOME/fugue`, or `~/.local/state/fugue` when `XDG_STATE_HOME` is unset |

Use absolute paths for predictable behavior across working directories. The state
directory contains `fugue.db` and `artifacts/`. It retains run progress,
operations, approvals, usage records and artifacts. Keep it available when
resuming. Artifacts have SHA-256 checksums. Local state and artifacts can include
repository content and provider output; protect them as you would the checkout.

## Limits

All integer fields under `[limits]` must be greater than zero.

| Field | Default | Meaning |
| --- | --- | --- |
| `max_repair_attempts` | `3` | Repair limit for one stage |
| `max_concurrent_runs` | `1` | Maximum concurrent runs |
| `database_wait_seconds` | `30` | Seconds to wait for the run database when another Fugue process is writing |
| `agent_timeout_seconds` | `3600` | Agent call timeout |
| `max_output_bytes` | `65536` | Output capture limit |
| `ci_poll_interval_seconds` | `20` | CI polling interval |
| `ci_timeout_seconds` | `3600` | CI waiting timeout |
| `overview_max_tickets` | `100` | Most open tickets `fugue overview` reads |
| `overview_cache_seconds` | `300` | Seconds a cached `fugue overview` summary stays valid |
| `infrastructure_markers` | See below | Strings used to recognize infrastructure failures |

Default infrastructure markers are `runner lost`,
`the runner has received a shutdown signal`, `econnreset`,
`temporary failure in name resolution`, `503 service unavailable` and
`no space left on device`. A supplied list replaces the default list. Entries
are stripped and case-folded; blank entries are invalid.

## Repository policy

| Field under `[policy]` | Default and meaning |
| --- | --- |
| `commit_subject_max` | `72`; positive maximum subject length |
| `autonomy_profile` | `human`; also accepts `campaign` |
| `gate_commands` | The argument arrays below |
| `size_rules` | The per-Size defaults below |

Default gate configuration:

```toml
[policy]
gate_commands = [
  ["uv", "run", "ruff", "format", "--check", "."],
  ["uv", "run", "ruff", "check", "."],
  ["uv", "run", "mypy"],
  ["uv", "run", "pytest"],
  ["uv", "build"],
]
```

Each command is an argument array, not a shell command string. Replace the list
with the target repository's gate; required tools must already be installed.

| Size | `analysis` | `review` | `max_components` |
| --- | --- | --- | --- |
| XS | `false` | `false` | `1` |
| S | `false` | `true` | `1` |
| M | `true` | `true` | `2` |
| L | `true` | `true` | Unset (no maximum) |
| XL | `false` | `false` | `0` |

Override a Size with a table such as `[policy.size_rules.S]`, supplying `analysis`
and `review` booleans and, optionally, `max_components`. Omitted Sizes retain
their defaults. TOML has no null literal; omit an optional field to leave it unset.

The default `human` profile preserves developer solution selection and explicit
approval. `campaign` represents the repository's
[autonomy exceptions](../dev/process.md#autonomous-development-campaign-exceptions);
choosing the value is not a substitute for actual consent or credentials.

### Interview configuration

The interview uses the analyst role (default provider `claude`). The stand-in
role is used only with `fugue question --automatic` and is omitted from the
`fugue run` role table and its authentication warnings.

| Role | Default provider | Default tier for Size L |
| --- | --- | --- |
| `stand_in` | `codex` | `medium` |

Configure it with `[roles.stand_in]` (`provider`, `model`, `tier`,
`authentication_mode`, `fallback`) and `[policy.tier_rules.stand_in]` (`L`).

| Limit under `[limits]` | Default | Meaning |
| --- | --- | --- |
| `interview_max_turns` | 10 | Positive maximum of person or stand-in messages plus analyst replies; the initial analyst reply is additional. |
| `interview_call_timeout_seconds` | 300 | Positive timeout for each analyst or stand-in call. |
| `interview_automatic_max_seconds` | 1800 | Positive wall clock bound for the whole automatic interview; expiry requires human continuation. |

Every interview call records usage under `analyst` or `stand_in`; inspect it with
`fugue usage --by role`. A stand-in never selects an Analysis option.
