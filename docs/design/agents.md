# Agents

## The roles

There are five agent roles. Three analyse, one implements, one reviews. The developer named the three analysis agents: "1. Interviewer (walks through and explores with the user about ideas), 2. Logic Builder (creates logic based on what was decided on L, creates a few versions, the USER checks again, but mainly just picks), 3. Implementation Analysers (more concise, doesn't explore that much, it READS THE LOGIC, and sees which files and how it needs to be updated and creates a detailed plan for it). Then the Implementation agent reads it and develops."

| Role | Setting name | Tier | Reads | Writes | May ask the developer |
| --- | --- | --- | --- | --- | --- |
| Interview | `interview` | L | the ticket, code, the project's decisions | the record, the decisions, a proposal of M tickets | yes, all the time |
| Logic | `logic` | M | the ticket, cited constraints, earlier logic, code | versions, the logic file, a proposal of S and XS tickets | only when it cannot go on |
| Plan | `plan` | S, XS | the ticket, the cited logic, code | the unit's plan | never |
| Implementation | `implementation` | S, XS | the ticket and the plan | code, uncommitted | never |
| Reviewer | `reviewer` | S | the unit's diff, the ticket, the plan, the CI result | findings | never |

**Built.** The prompts, the templates and the roles exist. **Open.** Nothing sends a prompt to a model yet. The agent layer, which runs a job on an SDK, is not built.

## What an agent is told

An agent's instructions are four layers, in this order. The first two are always there.

1. **`system.md`**: general rules for writing and communicating with the developer, the same for every agent.
2. **The role prompt**: `agents/<role>.md`.
3. **The developer's own guidance** for that role, if they set one, under a heading `## Additional guidance` and inside `<additional_guidance>` tags. It adds to the instructions and never replaces them.
4. **The template** the output must follow, which comes with the job.

The prompts are general on purpose, so they can be reused, and they name no project. The developer's words: "These are supposed to be generic for reuse not specific. I want each agent to have a configurable prompt, aka we can attach extra guidance on top of these general ones (They always stay as a system prompt in a way)."

**Built.** The guidance is a setting: `agents.interview`, `agents.logic`, `agents.plan`, `agents.implementation` and `agents.reviewer`, each the path of a Markdown file. A relative path is from the repository root, and a missing file is an error. They are not local-only: they can be set globally.

### What system.md says

- The reader did not watch the work, so the writing has to stand on its own. Give just enough context.
- Lead with the outcome. Readable beats short: choose what to include, and never compress into fragments, abbreviations or arrow chains. One idea to a sentence, paragraphs of three lines or fewer, one term for one thing, no invented labels.
- Say what is true: fact and inference apart, nothing invented, exact places as `path:line`, partial work never presented as complete, conflicts in the instructions surfaced.
- Use the project's glossary exactly, and list a missing term as unresolved.
- Match the form to the question. Write each paragraph and list item as one line. No emojis. A code comment only states a constraint the code cannot show.
- In anything published (tickets, comments, pull requests, commit messages) name roles and never a tool, a model or a provider.
- Follow a given template exactly.

### What each prompt adds

- **Interview.** Probe intent, users, boundaries, failure cases, constraints and the code and libraries touched. Pick the control that fits each question. Offer three to five directions, then signal readiness. Return the record, the decisions as constraints with ids, and the proposed M tickets. Propose M tickets only.
- **Logic.** Build the logic around flows and diagrams. Offer short versions that differ only in what differs, with what each costs in units. Recommend one. Never select. After the pick, write the full logic for that version only, then propose the units. Reuse earlier logic by reference. For a bug, explain the logic gap and give example fixes.
- **Plan.** Brief. Find where the change is expected and the project's existing way of doing it. Say what each place changes to. Add scenarios the ticket does not state, from the logic's edge cases. Name what must not change. For an XS, only the changes and the verification. Promote one level when logic is missing, and stop.
- **Implementation.** Make the change the ticket and the plan describe. The plan's places are a starting point and not a limit: change what the work needs and list the extra files in the final message. Stop only for a decision that is not yours. Simplest code that makes the scenarios hold. See a new test fail before the change and pass after. Protect changes you did not make, and run no destructive command. Leave commits and the gate to the orchestrator. Work on the branch and in the worktree given, and build on earlier units' commits.
- **Reviewer.** Review only the unit's commits. Read the diff, the ticket, the plan and the CI result as given. Check each hunk and the function around it. Verify every finding before reporting it. Two kinds of finding. See [Review and follow-ups](review-and-follow-ups.md).

The prompts were improved from a comparison with published ones: OpenAI's Codex prompt and prompting guide, the extracted Claude Code prompts, two published code-review agents, and Anthropic's prompting guide. What was adopted: "readable beats short", read before claiming, find the project's existing conventions, protect others' work, simplest code that makes the scenarios hold, verify findings as confirmed, plausible or refuted, say what to do and not only what not to do, and wrap injected content in tags.

## Tools for asking the developer

**Built (core), Open (adapters).** An agent that may ask uses tools that show a question in whichever interface runs the flow. They are defined once, apart from any agent, as a name, a description, a JSON schema and a function. An adapter for an agent SDK wraps each one. The developer: "We need to give the agents tools to interact with the user. For example Analysis agents should have the ability to prompt the user for questions, that's why we have the UI so that is KEY."

| Tool | Question shown | Answer returned |
| --- | --- | --- |
| `ask_user` | pick one of several, with an optional Other field | the choice, and whether the developer typed it |
| `select_from` | tick any number, with an optional Other field | the list, and whether the last was typed |
| `ask_user_to_write` | free text | the text |
| `ask_user_form` | several related questions, answered together | an object keyed by each field's name |
| `tell_user` | something to read and acknowledge | acknowledged |
| `ready_to_analyse` | "Start the analysis?", default no | proceed or keep talking |

- The interview agent gets all six. The logic agent gets the first four. The plan, implementation and reviewer agents get none.
- A bad call returns an error the agent can read and fix. A limit on questions can be set, and past it a tool tells the agent to signal readiness.
- The screen labels each question with who is asking ("interview asks").
- The old Fugue interview used the same kinds of request: select, checkbox, confirmation, typed answer and information. They map to Ask, Select, Ask with yes and no, Chat and Ask with a single `ok`.

**Open.** Claude takes in-process tools. Codex would need a small local MCP bridge to the running screen. The interview and logic roles are to run on Claude first. **Open.** The logic agent also needs read-only tools to find and read related tickets, so it can find earlier logic to build on. They need the GitHub layer's search and sub-issue reads.

## What the SDKs provide

Checked against the installed SDKs and their documentation, not from memory.

- **Claude Agent SDK 0.2.189** drives the Claude Code process, so it brings Claude Code's built-in tools: Read, Edit, Bash, Glob, Grep, LSP, NotebookEdit, subagents and plan-mode tools, among others. Control comes from `tools` and `disallowed_tools` (which built-ins exist), `allowed_tools` (pre-approval), `permission_mode` (`default`, `acceptEdits`, `plan`, `bypassPermissions`, `dontAsk`, `auto`), `can_use_tool` and `hooks` (inspect or block each call), `sandbox`, `cwd`, `mcp_servers` (in-process custom tools as `mcp__<server>__<tool>`), `output_format` (a JSON schema, for typed results), `agents` and `max_turns`. Under `bypassPermissions`, `allowed_tools` restricts nothing, so use `disallowed_tools`.
- **OpenAI Codex SDK (`openai-codex` 0.172.0)** drives the local Codex app-server, which has its own built-in shell and patch tools. The SDK offers `Sandbox` (`read_only`, `workspace_write`, `full_access`), `ApprovalMode` (`deny_all`, `auto_review`), `cwd`, `developer_instructions` and a per-turn `output_schema`. There is no per-tool selection in the surface inspected.
- **PydanticAI's `claude-agent:` model** switches Claude Code's own tools, settings, skills and MCP servers off, and only PydanticAI function tools reach the model. A Claude agent through it could not read a file or run a command unless the tools were written. **Agreed.** Agents therefore run through the SDKs directly, behind Fugue's own interface. PydanticAI stays out of agent runs. pydantic-graph stays for the flows.

What each role would get:

| Role | Claude SDK | Codex SDK |
| --- | --- | --- |
| Interview, logic, plan, reviewer | Read, Glob, Grep, LSP, read-only git; Edit, Write and other Bash hidden | `Sandbox.read_only` |
| Implementation | Read, Edit, Write, Glob, Grep, Bash in the worktree; a hook refuses `git commit`, `git push` and `gh` | `workspace_write` with `deny_all` |

"No commits" is enforced with a hook and not only the prompt, because a prompt can be ignored. Codex has no hook equivalent that was found. A worktree's git data lives outside its folder, so the sandbox may already block commits. That is untested.

## The agent interface

**Agreed, not built.** The developer: "make the code such that all of the logic is separate from which agent uses it ... something easy like claude.run(Logic) (Very VERY rough example do not use)". The shape:

- A **job** names its role, its input, the template its output must follow and whether it may write.
- An **agent adapter** runs a job on one SDK and returns the typed result.
- **Configuration** picks the adapter per role, so Claude or Codex can take any role.
- The result is checked against the template with `Template.missing`, and an answer that fails can be sent back to be fixed.
- Started and finished agents and their output go to the reporter as `AgentStarted`, `AgentFinished` and `Output(stream="ai", agent=...)`, which the AI window shows.

## Outputs: responses and artifacts

The developer's definition: "a RESPONSE and an ARTIFACT. Response: directly for the user's eyes, nicely formatted, easy to read, give just the right context, not too much not too little. This is the ticket body. Artifact: these are solution files, meant for agent-to-agent communication to give pass down instructions." The templates are mandatory and not adjustable for now: "templates the agent HAS TO FOLLOW".

**Built.** They are Markdown files in `src/fugue/prompts/`, loaded by `core/agents/prompts.py`. `Template.missing(answer)` lists the headings an answer lacks or has out of order. A heading with a placeholder repeats one or more times (a version, an option). A heading marked `<!-- optional -->` may be left out.

### Responses

- `responses/ticket-format/<type>.md`: how a ticket is created. `feature`, `bug`, `maintenance` (M); `task`, `test`, `documentation` (S, XS); `question`, `incident` (L). They hold Summary and Scope and leave Analysis, Solution and Details as placeholders for an agent to fill. The M templates have `Builds on` and `Agreed in #<L>`. The unit templates require `Agreed in #<M>` with constraint ids. The Bug template has **The logic gap** and **Example fixes**. The unit templates say Analysis, Solution and Details are not applicable.
- `responses/logic.md`: what the logic agent adds to an M ticket. Analysis has the decision as one sentence (context, problem, recommendation, quality, accepted downside), where we are with `Builds on`, optional drivers, one block per version with a Mermaid diagram, what it does, watch out and the work it takes, then the recommendation. Solution has who selected, when and why, the logic file link, and the units table. Details has key points.
- `responses/question.md`: what the interview adds to an L ticket. The same decision sentence, where we are, directions, recommendation. Solution has the decision, who selected, the **agreed constraints with ids**, consequences. An optional Children table lists the M tickets with the logic each builds on and the constraints it carries.

### Artifacts

- `artifacts/logic.md`: the specifics of an M's logic. Builds on (optional), Flow (Mermaid), States (optional), Rules (a table), Edge and failure cases (a table), Constraints with ids, Context (optional).
- `artifacts/plan.md`: the unit's plan. Context (optional), Changes (where it is expected, not a limit), Scenarios (optional, what the ticket does not state), Do not change (optional), Verification (command, expected output, what it proves).


## Research behind the templates

The structure of the plan, the logic response and the Question response was checked against published formats. What informed the choices: OpenAI's execution-plan format (self-contained, observable acceptance, explicit repository context), GitHub Spec Kit and Kiro specs (scenarios with ids that trace to checks), GitHub's study of agent files (commands first, examples over explanations, three-tier boundaries), Anthropic's multi-agent write-up (a brief needs an objective, an output format, tool guidance and boundaries), MADR and Y-statements (a decision stated in one sentence, drivers, consequences), and Google-style design documents (real alternatives, including doing nothing).

The developer rejected one element: the list of allowed files. And added one rule that governs all of it: the ticket and the file must not duplicate each other.
