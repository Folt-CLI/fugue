# Review and follow-ups

## The two kinds of finding

**Agreed.** The developer switched to blocking and non-blocking: "we have 2 discoveries, BLOCKING, NON_BLOCKING. Blocking means an issue that is very obvious and breaking, non-blocking means that its a very thin edge case and it can be handled at a later date, this doesn't mean we forget it, it has to be made into a ticket." There is no limit on review rounds: "Review doesn't have strict limits".

**Blocking.** A confirmed defect in code the unit touched, which is obvious and breaks something: a wrong result that matters (lost data, a security hole, incorrect output, a crash in normal operation), reachable in a scenario of the ticket or in ordinary use. The reviewer gives the steps and inputs that reproduce it. At most three per round, ranked. Blocking findings go back to the implementer. Then the gate runs, then the review runs again.

**Non-blocking.** A thin issue that can wait, possibly for a later release. At most five per round. They never stop a unit, and they are never forgotten.

## Who reviews what

- Only an S is reviewed. An XS is not, so it never produces findings.
- The reviewer reads the unit's commits only, with the ticket, the plan and the CI result as given. It runs nothing and uses no network.
- It reports only what the project's lints and the gate would not catch.
- It verifies each finding before reporting it: confirmed (name the inputs and quote the line), plausible (a real mechanism with an uncertain trigger, reported as non-blocking), or refuted (dropped).
- It does not repeat a finding that was rejected, unless it can show the reason does not hold.
- A change that no scenario needs, in files the plan did not name, is reported as scope creep: non-blocking unless it breaks something.
- A finding about something outside the unit's diff is not part of the review. It is reported separately, so it can become an Incident.

## Two kinds of non-blocking finding

The developer: "i expect non-blocking issues to be something simple and not logic binding, example: a typo or something in a string. I've seen this happen. I want these caught of course, I'm just not sure where to file them. IF ITS A LOGIC GAP, then that does need its own separate ticket with M size I agree."

The reviewer labels each non-blocking finding as one of two kinds. When in doubt, it is a gap.

| Kind | What it is | What the reviewer gives | Where it goes |
| --- | --- | --- | --- |
| Nit | The fix changes no behaviour and needs no decision: a typo in a string, a wrong word in a document, a misnamed variable. | The exact change: file and line, current text, new text. | An XS ticket in the backlog |
| Gap | The fix means choosing how something should behave: an edge case the logic does not cover. | The gap: which case, what happens now, what should happen, and one or two example fixes. | A Bug ticket (size M) in the backlog |

## Where they are filed

**Agreed.** The developer decided: "Have them in the backlog, and when relevant M tickets get created they can be moved." The reason follows from a rule of the process: a parent waits for its closed children. A non-blocking finding may be handled much later, possibly in a later release, so it can never be a child of the M it came from, of that M's L, or of a release Epic. Any of those would hold the release open.

- **Milestone:** `Backlog`, which the process already uses for work that is not scheduled in a release.
- **Parent:** none. They stay loose until a relevant M exists, and then they are moved under it.
- **Priority:** low. **Status:** `S0: To Do`.
- **Origin:** the body says where it was found, as a plain reference: the unit ticket, the commit, the file and line. This traces the origin and blocks nothing.
- **Nits:** an XS ticket whose type follows the file: Documentation for documents, Test for tests, Task for anything else. They are filed one per review round per file type, with every exact fix listed. The developer has not objected to this grouping and has not confirmed it. **Open.**
- **Gaps:** a Bug ticket, which is an M, with the logic gap and example fixes in the Bug template. Each is picked up later like any M, by building its logic.
- **Dedupe:** the backlog is searched first. An existing ticket for the same thing is linked and nothing new is created.
- **Outside the diff:** a finding about code the unit did not touch becomes an Incident, as the process documents say. A tiny nit in untouched code is not reported at all.

An XS in the backlog without an M parent is an inbox item. `fugue work` refuses it with a clear message until it is attached to an M, because every S and XS needs an M parent.

## When and by whom

**Agreed.** Fugue files them and never the reviewer, right after a review round. Filing is tagged as a permission, so an automatic run files without stopping, and an interactive run shows the list first with yes preselected.

The record: one short comment on the unit's ticket listing what was deferred ("Deferred to #31, #32"), and a follow-ups line in the M's pull request description. Nothing is posted on the pull request itself, because the review reads commits and not a pull request.

## Later: triage

When a relevant M is created, backlog tickets can be attached to it. A `fugue triage` command could do it. It is not planned for now.

## What needs building

- A GitHub action to create an issue with a type, a milestone and the Priority field.
- A GitHub action to attach an issue under a parent (native sub-issues) and to add a blocked-by link. githubkit has both.
- A search of open tickets for duplicates.
- The reviewer's typed output: kind, file, line, what goes wrong, reproduction for blocking, exact change or example fixes for non-blocking.
