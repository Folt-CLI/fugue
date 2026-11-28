# Status, decisions and open points

## What is built

| Area | State | Commit or note |
| --- | --- | --- |
| CLI skeleton with Typer, commands in one file each | Built | `cli/commands/` |
| GitHub layer: issues, pull requests, labels, branches, repository, projects | Built | `core/github/` |
| `fugue github ...` commands with Rich views | Built | `cli/views/` |
| Git operations with pygit2: repository, worktrees | Built | `core/gitops/` |
| Async process runner, with stderr streaming | Built | `core/process/` |
| Reporter, events, questions, answers | Built | `core/reporting/` |
| Flow base, steps, actions, checkpoint, run records | Built | `core/flow/` |
| Settings layers and `fugue config` | Built | `config.py`, `configfiles.py`, `core/settings.py` |
| Terminal screen: checklist, ticket, STDOUT, STDERR, AI windows, status bar | Built | `cli/tui/` |
| Questions: Ask, Select, Chat, Form, Other, hold and resume | Built | `cli/tui/action/` |
| Run states: Running, Stopped, Finished | Built | `core/reporting/events.py` |
| `--headless`, `--auto`, `--quiet`, `--project` | Built | `cli/commands/work.py` |
| Worktrees under `.worktrees` | Built, per ticket (to become per M) | `core/gitops/worktrees.py` |
| The gate, with its checks listed in the checklist | Built | `core/flow/actions/run_gate.py` |
| The project's fields under the ticket | Built | `core/flow/actions/read_details.py` |
| Mermaid diagrams drawn as text in the terminal | Built | `cli/views/markdown.py`, with `termaid` |
| Agent prompts, templates, the loader and `Template.missing` | Built | `src/fugue/prompts/`, `core/agents/prompts.py` |
| Question tools for agents | Built in core, no adapter | `core/agents/tools.py` |
| The work flow | Built, older routing | `core/work/` |

The work flow runs: read the ticket, read its project fields, move the status, prepare the branch, then the stages for the Size, with the gate running and the agent stages saying "no agent set".

## What is agreed and not built

In the order the developer's decisions suggest building them.

1. **Route by type.** Epic is refused. Question and Incident go to the L flow. Feature, Bug and Maintenance go to the M flow. Task, Test and Documentation go to the unit flow. A mismatch of type and size stops with a message.
2. **The unit's parent check.** Read a ticket's parent, and refuse an S or XS with no M parent.
3. **One worktree and one branch per M**, a lock per M, units as commits with the tag rules, a draft pull request after the first push.
4. **The agent layer.** Jobs, adapters for the Claude SDK and the Codex SDK, the hook that refuses commits, structured output, the template check with a retry.
5. **The unit flow end to end.** Plan, implement, gate with repair, review for an S, commit and push.
6. **Review follow-ups.** The reviewer's typed output, filing nits and gaps in the backlog, dedupe, the comment on the unit.
7. **The M flow.** Gather constraints and earlier logic, versions, the pick, the full logic, the preview, creating units.
8. **The L flow.** Interview, decisions, the preview, confirming and creating M tickets.
9. **GitHub additions.** Create with a type and milestone, parent and blocked-by links, search, apply an analysis to a ticket, attach a file, draft pull requests.
10. **Run records read back.** List runs with their state, resume from the step where a run stopped.

Later, and not part of `fugue work`: a separate command that plans the unblocked units of an M in one run.

## The decision log

Everything the developer decided, in order. A quotation is the developer's own words. "Superseded" marks a decision a later one replaced.

### Starting over

1. Move `src` to `src-temp` and ignore it. Create a new `src`. "we will go slowly creating what i want exactly, not tickets, just pure work slowly". The old code is scraped for what can be reused.
2. The project goal stays the same, and the developer has a more direct impact on the development.
3. The smallest end to end is one ticket, called `fugue work`.
4. A directory to test from: `fugue-tester`, a clone of fugue on `develop`. It is used to test the pre-refactor code and the developer does not care about its history.
5. Use the real GitHub. The branch is renamed `refactor`. It carries the old base ticket (#242), and the commits use `#0`.
6. Tests are not a focus for now.
7. A real CLI library instead of argparse: Typer. The structure follows `src-temp`: `cli/commands/` with one file per command, and other files beside it.
8. Version 0.4.0.
9. Reuse from `src-temp` one piece at a time. The async process runner is copied as it is, and kept because it is tested.

### Libraries and the SDKs

10. Git and GitHub use SDKs and not the command line: pygit2 and githubkit. Look up libraries on the web and PyPI every time. githubkit was chosen after asking how well it is maintained.
11. The agents run through their SDKs, the OpenAI Codex SDK and the Claude Agent SDK, which allow the use of both subscriptions. Added with the same pin policy.
12. The GitHub token comes from an environment variable and not from `gh`.
13. PydanticAI over LangChain, and Pydantic for the classes, for typed outputs. PydanticAI is pinned to the branch that has the subscription model. **Superseded in part:** that model switches Claude Code's tools off, so agents run through the SDKs directly. See [Agents](agents.md#what-the-sdks-provide).

### Architecture

14. `Github` is a class with areas, easy to use.
15. No functions in the open and no global variables. Classes. Settings use a Pydantic base class that is passed into separate things.
16. `repository.py` goes under `gitops/`.
17. A `fugue github issue ...` interface, an abstraction of the Github class, with nice Rich output. Then everything that might be needed for GitHub, and projects.
18. A core, so that the command line only calls it and a backend or MCP side is possible later. A first attempt, a wrapper, was a misunderstanding and was thrown away. The core holds the whole workflow and the handling of exceptions. The command line prints, handles raised exceptions and starts a flow.
19. A reporter with `report` and `ask`, with an option to suppress output.
20. A `views/` directory for the output, a class per area, each imported directly. Force push after each commit, at that time.
21. The work flow becomes a pydantic-graph of class nodes.
22. A common layer for all flows: a Flow base, a generic Step, reusable actions apart from the per-flow nodes, shared state and dependencies, event scoping by run and flow, and run records on disk.

### The interface

23. Build the terminal interface now, with Textual.
24. A status bar at the bottom showing the current step. The checklist controllable through shortcuts. A ticket window, an AI log window, a checklist window, and a small floating window for actions. Windows share a class through inheritance.
25. Three outputs, STDOUT, STDERR and AI, as separate log windows. Later they became pages, one at a time, "so they are not in one screen".
26. Select and move with Shift+H J K L. Arrow keys switch agents in the AI window. A visible focus border.
27. `--headless` runs without the screen.
28. A yes or no between every step. A separate window at the bottom, with a body and text easy to change. Nicer buttons, and arrow keys to move through the options.
29. More kinds of question: select (multiple), chat, and an Other option that is a text field and a dynamic selected option. Then a Form, in a window of its own, with a separate Submit button.
30. Worktrees go inside the repository, in `.worktrees/<branch>`, which is git-ignored.
31. The AI window lists the agents in a vertical bar to switch between what each did.
32. `--auto` answers the permission checks, and questions carry a tag so that anything purely a permission can be skipped.
33. Settings: easy to set the project in a workflow. A `.fugue/config.toml`, `fugue config set/get/list`, global and local layers. Some settings cannot be global.
34. Run states: Running, Stopped, Finished. `r` brings the question back. No as often as wanted. More ways to stop will exist.
35. The old `fugue.toml` is no longer read.
36. A gate's checks are shown in the checklist.
37. The project's fields under the ticket: status, size, type, priority, component, disposition, milestone, assignee.

### The work model

38. A ticket's behaviour through work is defined by its Type, Size and Components. The types are worked through first.
39. Task, Feature and Maintenance first. **Superseded** by the tier model below.
40. Sizes: M, then S, then XS. **Superseded.**
41. Triggers asked the developer on M. Analysis alone may promote a ticket, and XS never changes. **Superseded:** triggers are gone, and XS may be promoted to S.
42. A preview window asks whether to approve an analysis, and on yes it is applied to the ticket on GitHub and refetched. **Agreed.**
43. Blocking and non-blocking findings. Non-blocking ones become tickets. No strict limit on review rounds.
44. The agents are configurable, and the logic is separate from which agent runs it.
45. An `agents/` directory with generic prompts, a configurable extra prompt appended, two kinds of output (response for the developer, artifact for agents), templates the agent must follow and that are not adjustable for now, and a `system.md` of general rules for all agents.
46. Research what others do, then compare, then apply. Done, and applied.
47. Agents get tools to ask the developer. The analysis for an L is more interactive.
48. Read the docs of the old Fugue and of `~/Coding/foltivo/folt`, "which holds the most up to date way of working for me".
49. Scoping is removed. S goes analysis, implementation.
50. No duplicated information between the ticket and the unit's file. No allowed-files list.
51. The developer first said the solution should not hold what to change, where, what to verify and how: "A LOT OF TOKENS for nothing". **Superseded.** The developer changed their mind: "We have plan.md which will carry what to change and where". The unit's file is `plan.md` and the M's file is the logic.
52. Three tiers and three analysis agents: L interview (exploratory), M logic (details the logic of a feature or bug fix), S and XS units of work (details about implementation).
53. S: analysis, implementation, review. XS: analysis, implementation.
54. `fugue work` is a single ticket. `fugue work M` creates the S and XS tickets, and the work is done with `fugue work S` or `XS`.
55. An L cannot create S or XS tickets. It creates M tickets. The list of M tickets is not uploaded to the ticket.
56. S and XS are always automatic. No triggers. An S or XS must have an M parent. An M needs no L parent. XS can be promoted to S.
57. An M names the earlier logic it builds on, only when that logic was built before.
58. Information must be passed down without repeating it wastefully.
59. Mid-tier models for now. Optimisation later.
60. Types by tier: Epic is XL, Question and Incident are L, Feature, Bug and Maintenance are M, Task, Test and Documentation are S and XS. An Incident's M tickets are Bugs. A Bug explains the logic gap and gives example fixes.
61. Branching: an M creates `feature/5-...`, `bug/5-...` or `maintenance/5-...`. S and XS are commits on that branch, and a Task carries the M's tag.
62. Non-blocking findings are not handled in the M. They are filed in the backlog and moved under a relevant M when one is created. Logic gaps get their own M-sized Bug ticket.
63. Unblocked XS and S tickets run through the same analysis agent, because it relies on the M ticket. **Superseded.** The developer decided grouping is not for now: it will be a different command, and `work` only targets one ticket.
64. Flows and diagrams are Mermaid, one source for GitHub and the terminal. GitHub draws it, `termaid` draws it in the terminal, and a diagram that does not fit the window is shown as its source.

### Unaddressed until this write-up

Two messages arrived while other work was running and were not answered at the time. They are included above (items 51 and 63), and the developer has since answered both: the plan carries what to change and where, and grouping is a different, later command. The write-up was assembled by reading every user message in the transcript.

## Open points

Each is a question that is still to be settled, or a disagreement between the code and a decision.

### Disagreements between the code and the decisions

1. **Confirm the logic template.** The developer's early sketch of the logic was Context, Scenarios and Examples, and later said flows and diagrams are key. The template has flows, states, rules, edge cases and constraints, and no Context, Scenarios or Examples sections. The sketch was set aside when the plan took over what to change and where.
2. **The routing is the older one.** It routes by the old types and sizes, refuses Bug, and treats M as an implementation ticket.
3. **The worktree is per ticket.** It is to be per M.
4. **The status moves to S4 for every ticket,** right after reading it.

### Decisions still to make

5. **Does an M still promote to an L?** It was agreed in the first design. The new model makes M a logic ticket.
6. **Is the preview approval a permission,** so `--auto` approves it? Is the confirmation of a list of tickets to create a permission?
7. **Statuses per tier and step.** And when a unit's ticket, an M and an L are closed, and with what Disposition. The old process said a parent cannot close until all its children are closed.
8. **When the M's branch is pushed,** and when its pull request is marked ready. Who merges stays the developer.
9. **Where the lock of an M lives,** and what happens to a stale one.
10. **The repair limit of the gate,** and whether reaching it re-sizes an S to an M, as the old process said.
11. **The interview's turn limit.** The old Fugue used 10.
12. **How nit fixes are batched.** One XS per review round per file type was proposed and neither confirmed nor rejected.
13. **How the unit's number goes in a commit** when the unit's ticket is promoted or split mid-run.
14. **How an attachment is uploaded** without `gh`: the logic file on an M, and the conversation record on an L.
15. **What Components change.**
16. **Whether to warn when a worktree outside the configured root is reused.** It was offered and not answered.
17. **Drift from the base,** and a sync step.
18. **A force-finish command** for a run. It was said to be for later.
19. **How a model is chosen per role,** and whether the tier is per ticket.
20. **A Codex bridge for the question tools,** or Claude only for the roles that ask.
21. **Triage:** a `fugue triage` that attaches backlog tickets to an M. Not planned.

### Facts not yet verified

- Anthropic's terms for using a subscription through the Agent SDK.
- Which `claude` and `codex` binaries the SDKs run. Each bundles about 170 MB.
- Whether the Codex sandbox blocks a commit in a worktree, because a worktree's git data is outside its folder.
- Writes against the real GitHub. The real response shape of creating a label.
- Projects owned by a user. Fetching over SSH.
- The source of the `GITHUB_TOKEN` in the developer's shell, which lacked the `project` scope.
- Whether a run's `Stopped` state and record can be resumed as designed. The records exist and nothing reads them.

## Facts learned along the way

- **Contributions.** Commits on `refactor` do not appear in the developer's GitHub contributions graph, because GitHub counts only commits on the default branch (`develop`) or commits that arrive there through a merged pull request. The commit email is linked to the account and the repository is not a fork, so merging `refactor` is what makes them count.
- **Caching.** Fugue has none. A run reads the ticket once and keeps it in the flow's state. The project field ids could be cached for one run if the extra requests ever matter.
- **Old worktrees.** A branch that is already checked out in another worktree is reused where it is. In `fugue-tester` two worktrees from the old layout (`fugue-tester-worktrees/245-...` and `.../246-...`) were reused that way. They were clean. They can be removed with `git worktree remove` so a new one is made under `.worktrees`.
- **Ticket #246 on the real board** was moved by an accidental test run, restored to `S0: To Do` with the developer's permission, and found on `S4: In Implementation` again later, most likely from a real run. It was left alone.
- **Installing.** `uv tool install --editable .` from the repository puts `fugue` on the path and runs from `src/`. `uv run fugue ...` works from the checkout without installing. The install was not run on this machine.

## A caution found while writing this

A listing script that prints each setting's value would print the token, because the value of `github.token` is read from the environment. `fugue config list` shows only "set" or "not set" for a secret, and nothing else should print it. Any new command or log must treat a secret the same way.
