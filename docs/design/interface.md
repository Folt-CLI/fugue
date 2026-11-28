# The interface

`fugue work N` opens a full-screen terminal interface when it runs in a terminal. Everything the flow says reaches it through the reporter, and it holds no logic of its own.

## Modes

**Built.**

- **The screen** is the default in a terminal. It is built with Textual.
- **`--headless`** (or `FUGUE_HEADLESS`) prints progress as lines instead. The developer asked for it as "an option called HEADLESS, this is without tui". It is also used with `--quiet` and whenever stdin or stdout is not a terminal, so scripts and continuous integration never open the screen.
- **`--quiet`** shows no progress, only the final line. Questions are still asked, because the flow cannot go on without an answer.
- **`--auto`** answers the questions tagged as permissions by itself.
- **`--project N`** sets the project for the ticket's status for one run.

## The screen

The developer's first brief: a Github Ticket window, an AI log window ("this can be for later"), a Checklist window that "shows everything there is for this workflow", a status bar at the bottom "showing the current step", the checklist controllable through shortcuts, and on each window "a smaller floating window that calls for actions from the user". The windows are to share one class through inheritance.

**Built.**

```text
+--------------------------------------------------------------------------+
| Header: fugue work #N                                                    |
+--------------------+-----------------------------------------------------+
| Checklist (c)      | Ticket (t) | STDOUT (o) | STDERR (e) | AI (a)        |
|  v Read the ticket |-----------------------------------------------------|
|  v Move the status |  the page in front, one at a time                   |
|  * Prepare ...     |                                                     |
|  o Run the gate    |                                                     |
|     o ruff check   |                                                     |
+--------------------+-----------------------------------------------------+
| Action needed: the question, its body, and its answers                   |
+--------------------------------------------------------------------------+
| Status bar: Running, step 3 of 5 | Stopped ... | Finished                |
| Footer: the keys                                                         |
+--------------------------------------------------------------------------+
```

- **One class for all windows.** `Window` is a framed, scrollable area with a title and a key. `ChecklistWindow`, `TicketWindow`, `LogWindow` (with `StdoutWindow` and `StderrWindow`) and `AiWindow` inherit from it. A window never knows about flows or events: the app feeds it.
- **Pages, not one crowded screen.** The developer found five windows at once too crowded: "dont you think we can create a better flow by separating them so they are not in one screen?" The ticket, STDOUT, STDERR and AI output are tabs, one in front at a time. The checklist stays at the side. They are tabs and not separate Textual screens because a screen that is not showing is not built yet, so events for it would be lost.
- **A dot marks a page** that got something while it was out of sight, until it is opened.
- **Three outputs.** The developer: "There is 3 different outputs: STDERR, STDOUT, AIOUT. These are all separate log windows." Process output and Fugue's own notes go to STDOUT, warnings and failures to STDERR, agent output to the AI window.

### Checklist

Shows every step of the flow from the start: pending, running with a spinner, done or failed, with the step's detail. A step made of parts, such as a gate's checks, lists them indented under it. The developer asked for this: "Speaking of gates, they need to also be shown in the check list".

- **Built.** The checklist starts with the steps every ticket has, and takes the steps of the ticket's pipeline when its Size is known (the `OutlineChanged` event), keeping the state of steps that already ran. A promotion sends the event again.
- The position in the status bar ("step 3 of 5") follows the checklist.

### Ticket window

**Built.** The ticket as GitHub has it, and under it a **Project** panel with every field the project has for that ticket. The developer asked for "status, size, type, priority, component, disposition, milestone, assignee". The first eight are listed in that order and the rest follow in the project's own order. When the run moves the status, the Status line changes to the new value.

### AI window

**Built.** Several agents can run in one flow, so the window has a vertical list of agents on the left and the log of the selected one on the right. The developer: "a separate vertical bar in the ai window so that i can switch between what was done".

- Each agent has a line with a spinner while running, a tick or a cross when it ends, its role under its name, and a yellow dot when it wrote while another agent was in front. An agent appears when the first event names it.
- Each agent's log is a column of lines that wrap at the real width, follows new lines while it is at the bottom, and stays put once scrolled up. It keeps the newest 5,000 lines.
- The window gets the accent border when focus is anywhere inside it.

### Keys

| Key | Does |
| --- | --- |
| `c` | show or hide the checklist |
| `t`, `o`, `e`, `a` | bring a page to the front and focus it |
| Shift+H, Shift+L | focus the checklist, focus the page in front |
| Shift+J, Shift+K | next page, previous page |
| arrows, PageUp, PageDown, `j`, `k` | scroll the focused window |
| Tab, Shift+Tab | move focus |
| `r` | call up the question the run is waiting on |
| Escape | put an open question away, or bring it back |
| `q` | quit |
| In the AI window: up, down (or `k`, `j`) | switch agent |
| In the AI window: `[` and `]` | previous and next agent from anywhere in the window |
| In the AI window: right (`l`), left (`h`) | into the log, back to the list |
| In the AI window: PageUp, PageDown | scroll the log from the list |

The developer first found only the mouse worked to select a window, and asked for "shift+hjkl", and later for arrow keys in the AI window.

## Questions

The developer: "we need to give the agents tools to interact with the user", and the questions must be a window at the bottom, not a pop-up in the middle: "I dont want it in the middle of the screen, i want it to be like a separate window at the bottom. Remember this will also have other types of questions, so make the Body and text easy to be changed."

**Built.** A question is a model with `text`, a Markdown `body` to read first, `tags` and `asked_by`. There are four kinds, and an answer is an `Answer` with `values`, whether the last value was typed into Other, and one answer per field for a form.

| Kind | Control | Keys |
| --- | --- | --- |
| Ask | one of several, as buttons that wrap onto more rows | left, right, Enter, the first letter of an option, down to the Other field |
| Select | a tick list | up, down, Space to tick, Enter to confirm, down to the Other field |
| Chat | a text box | Enter sends, Ctrl+J adds a line |
| Form | a window of its own that covers most of the screen, with the questions in a scrolling area and a separate Submit button | Tab, Shift+Tab, Ctrl+S, and answering one moves to the next |

- **Other.** An Ask or a Select can offer an Other option: a text field the developer writes in directly, which counts as chosen once it has text. The developer: "basically a text field the user can directly write in and that to be a dynamic selected option".
- **Form.** The developer: "a GROUP of questions to be asked, where the user can basically scroll through the questions in a group and submit all at once." It has its own window, "for the other types i guess they can stay at the bottom". "For the form i want a separate Submit button, that's how we finish it off."
- The question window has three parts, the text, the body and the answers, each set only from the question, so a new kind of question changes one method.
- The buttons have a rounded outline, muted until focused, with the letter that picks an answer underlined when no earlier answer uses it.

### Tags

- `PERMISSION`: the question only asks leave to go on. An automatic run answers it with its default, so such a question must default to yes. The developer: "We need a tag on questions, so that anything that is purely a permission can get skipped by this."
- `HOLD`: the run waits on the question quietly. The screen does not open it by itself, and the developer calls it up with `r`.

## Run states

The developer: "There is 3 states: Running, Stopped, Finished. When stopped i can click R to bring up the question i clicked NO to, i can continue clicking No as many times as i want, the only time we mark a Finished run is if it finished OR if we force finish it outside with a command (we dont have this yet its for later). There will also be other ways to stop a run so that is also something you need to keep in mind."

**Built.**

- A run is **Running**, **Stopped** or **Finished**, and nothing else. Every run event says which state it leaves the run in.
- **Finished** only by finishing. A force-finish command from outside is for later.
- **Stopped** has a reason. Today there are two: declined (the developer said no) and failed (a step raised an error). More are expected, such as an agent that needs help or a gate that cannot pass.
- A declined stop keeps the question: `r` brings it up and the developer can say no again as often as they like. A failed stop has nothing to answer.
- Closing the screen while stopped leaves the run Stopped. The command prints "Stopped before X. Run the command again to continue." and exits with code 1.

## Events

Core never prints. It reports these events, each stamped with the run id and the flow and recorded.

- Steps: `StepStarted`, `StepFinished`, `StepFailed`, and for the parts of a step `PartStarted` and `PartFinished`.
- Run: `RunStarted`, `RunResumed`, `RunStopped` (with a reason and a message), `RunFinished`.
- Content: `TicketRead`, `TicketDetails`, `StatusMoved`, `WorktreeReady`, `OutlineChanged`, `Message`, `Output` (stream `stdout`, `stderr` or `ai`, and the agent's name), `AgentStarted`, `AgentFinished`, `Answered`.

## Reporters

- **The terminal screen's reporter** forwards everything to the app, and can hold a question for later.
- **The headless reporter** prints with Rich and prompts for all four kinds of question. A choice can be typed in full or by its first letters. Ctrl-C or end of input stops the flow. It cannot hold a question, so a no ends the process.
- **A silent reporter** answers every question with its default answer.
- **The run reporter** wraps whichever reporter the caller gave. It stamps and records events, answers permission questions automatically in an automatic run, and passes `can_hold` through.
