# Shared skills and guidance

## What Fugue sends in prompts

Fugue keeps shared guidance in prompts and ships no skills. Its provider adapters
drive the unmodified provider command-line tools as child processes; installing
your own skills or guidance is separate from the guidance Fugue sends.

Claude runs with `-p` and a fixed tool allow list that does not include the Skill
tool. Skills installed for Claude would probably not load in Fugue's own runs;
this is an inference, not verified behaviour. Codex runs `codex exec` with only
sandbox, model, schema and output flags. That invocation does not establish how
Codex discovers or loads your guidance.

## Using your own skills with Claude

The following Claude Code facts were verified from the official documentation on
2026-11-15. They describe Claude Code's mechanisms; the inference above still
applies to Fugue's own runs.

| Mechanism | Location or behaviour |
| --- | --- |
| Project skills | `.claude/skills/<name>/SKILL.md` |
| Personal skills | `~/.claude/skills` |
| Enterprise skills | Managed by the enterprise |
| Plugin skills | Namespaced as `/plugin:skill` |
| Skill precedence | Enterprise over personal over project |
| Subagents | Project `.claude/agents` over user `~/.claude/agents` over plugin `agents/`; `--agents` outranks these locations |
| Plugins | Bundle skills, agents, hooks and MCP servers; a marketplace uses `.claude-plugin/marketplace.json` |
| Plugin install scope | User, project or local; each collaborator still installs a project plugin |
| Enabled plugins | Add their skill and agent descriptions to every session |

Claude reads `AGENTS.md` only when no `CLAUDE.md` exists in the working directory
or above. If a repository later adds `CLAUDE.md`, that stops this reading unless
`CLAUDE.md` imports `AGENTS.md` with `@`.

For skills shared across tools, use the portable
[Agent Skills format](https://agentskills.io/specification): a directory containing
`SKILL.md`, with these fields:

| Field | Portable requirement |
| --- | --- |
| `name` | Up to 64 characters, lowercase with hyphens, equal to the directory name |
| `description` | Up to 1024 characters |
| `license` | Optional |
| `compatibility` | Optional |
| `metadata` | Optional |
| `allowed-tools` | Optional |

Claude Code adds extra fields that other tools may reject or ignore. Keep shared
skills to the portable fields. Claude-specific settings described below apply to
Claude Code rather than the portable format.

## Using your own guidance with Codex

The Codex mechanisms below are not verified. This guide does not prescribe paths,
discovery rules or limits for them.

| Mechanism | Verification status |
| --- | --- |
| Skill directories | not verified |
| `AGENTS.md` discovery | not verified |
| `AGENTS.md` size limit | not verified |
| Custom agent files | not verified |
| Plugins | not verified |

## Token cost

In Claude Code, the name and description of each model-invocable skill are in
context every turn. The description plus `when_to_use` is truncated at 1,536
characters. This listing consumes context even before a skill is used. The skill
body loads only on use; about 500 lines is advised. Setting
`disable-model-invocation` removes the listing.

An enabled Claude Code plugin also adds its skill and agent descriptions to every
session. These context costs describe Claude Code; skill loading in Fugue's own
Claude runs remains an inference as explained above. Codex guidance token costs
are not verified.

## What Fugue never changes

Fugue never edits provider configuration or skill directories. It does not write
to `~/.claude` or `~/.codex`, and it does not edit project `.claude` or `.agents`
directories. You manage your own provider configuration, skills, agents and
plugins; Fugue keeps its shared guidance in prompts.
