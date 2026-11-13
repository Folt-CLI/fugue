"""The independent review stage: parsing, posting to GitHub, decisions and restart safety."""

import asyncio
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path

import pytest

from fugue.agents import (
    AccessMode,
    AdapterRegistry,
    AgentRequest,
    AgentResult,
    ScriptedAdapter,
    ScriptStep,
    UsageRecord,
)
from fugue.artifacts import ArtifactStore
from fugue.config import FugueConfig
from fugue.engine import Engine, EngineDeps, RunOutcome
from fugue.engine.facts import branch_name, worktree_path
from fugue.engine.review import (
    CLEAN,
    Decision,
    DiffLines,
    Finding,
    RoundEntry,
    build_review,
    diff_ranges,
    marked_finding,
    parse_findings,
    place_finding,
    review_prompt,
    review_repair_prompt,
    validate_decisions,
)
from fugue.github import CheckResult, PullRequest
from fugue.github.client import (
    PostedReview,
    ResolvePermissionError,
    ReviewComment,
    ReviewThread,
    ThreadComment,
)
from fugue.gitops import Git
from fugue.models import (
    AuthenticationMode,
    Component,
    IssueType,
    Priority,
    Role,
    Size,
    Status,
    Ticket,
)
from fugue.process import run_command
from fugue.store import Store
from fugue.workflow import RunState

SCOPE = "You may touch only these files:\n- `allowed.txt`\n\nImplement the change."
LINES = "one\ntwo\nthree\n"
FIXED = "one\nTWO\nthree\n"
BLOCKING = "## Blocking\n1. High: Crash on empty input. `allowed.txt:1`\n"
RANGE = (
    "## Blocking\n1. Medium: Wrong second line. `allowed.txt:2-3`\n```suggestion\nTWO\nTHREE\n```\n"
)
NON_BLOCKING = "## Non-blocking\n- Low: Rename the helper. `allowed.txt:2`\n"


def parsed(text: str) -> list[Finding]:
    result = parse_findings(text)
    assert isinstance(result, list)
    return result


# ---------------------------------------------------------------- pure review helpers


def test_exact_no_findings_is_clean() -> None:
    assert parse_findings("  No findings.\n") is CLEAN
    assert parse_findings("## Blocking\n- None\n\n## Non-blocking\n- None.\n") is CLEAN


@pytest.mark.parametrize("heading", ["Blocking", "Non-blocking"])
def test_unbulleted_section_text_is_one_medium_finding(heading: str) -> None:
    text = "High: Empty input crashes. src/a.py:10"
    [finding] = parsed(f"## {heading}\n{text}")
    assert finding.severity == "Medium"
    assert finding.blocking == (heading == "Blocking")
    assert text in finding.summary
    assert finding.path is None


@pytest.mark.parametrize("heading", ["Blocking", "Non-blocking"])
def test_empty_heading_is_clean_only_without_other_text(heading: str) -> None:
    assert parse_findings(f"## {heading}\n\n") is CLEAN
    assert parse_findings("## Blocking\n\n## Non-blocking\n") is CLEAN
    for text in ["Unexpected output", "None", "No findings.", "```text\nCrash\n```"]:
        assert parsed(f"{text}\n\n## {heading}\n")
        assert parsed(f"## {heading}\n{text}")


@pytest.mark.parametrize("text", ["No findings. Looks good.", "all fine", "", "   "])
def test_unparseable_text_is_one_medium_blocking_finding(text: str) -> None:
    [finding] = parsed(text)
    assert (finding.index, finding.severity, finding.blocking) == (1, "Medium", True)
    assert finding.path is None
    assert text.strip() in finding.summary or text.strip() == ""


def test_parse_locations_ranges_suggestions_and_limits() -> None:
    text = (
        "## Blocking\n"
        "1. High: Crashes on empty input (`src/a.py:10`)\n"
        "   Trigger: pass []. Fix: return early.\n"
        "2. **Medium**: Wrong total src/b.py:4-6\n"
        "```suggestion\n    total = 0\n```\n"
        "3. Low - no location here\n"
        "4. High: A fourth blocking finding is dropped. src/c.py:1\n"
        "\n## Non-blocking\n" + "".join(f"- Style {n}. src/d.py:{n}\n" for n in range(1, 8))
    )
    findings = parsed(text)
    assert [f.index for f in findings] == list(range(1, 9))
    first, second, third = findings[:3]
    assert (first.path, first.start_line, first.end_line) == ("src/a.py", 10, 10)
    assert first.severity == "High" and first.blocking
    assert first.summary.startswith("Crashes on empty input")
    assert "Trigger: pass []" in first.summary
    assert (second.path, second.start_line, second.end_line) == ("src/b.py", 4, 6)
    assert second.suggestion == "    total = 0"
    assert second.severity == "Medium"
    assert third.path is None and third.severity == "Low"
    rest = findings[3:]
    assert len(rest) == 5 and not any(f.blocking for f in rest)
    assert all(f.severity == "Low" for f in rest)


def test_parse_reversed_range_is_ordered() -> None:
    [finding] = parsed("- High: Bad. src/a.py:9-3\n")
    assert (finding.start_line, finding.end_line) == (3, 9)


@pytest.mark.parametrize("language", ["python", "", "text"])
def test_ordinary_fences_are_preserved_in_findings_comments_and_repairs(language: str) -> None:
    block = (
        f"```{language}\nparse([])\n\n    indented = True  \n"
        "## Non-blocking\n1. Low: code.py:99\n```"
    )
    [item, following] = parsed(
        "## Blocking\n1. High: Crash.\n"
        f"{block}\nReproduce with empty input. `src/a.py:2`\n"
        "```suggestion\nreturn []\n```\n"
        "2. Medium: Another defect. `src/a.py:3`\n"
        "## Non-blocking\n- None\n"
    )
    assert item.summary == f"Crash.\n{block}\nReproduce with empty input."
    assert item.location == "src/a.py:2"
    assert item.blocking and following.blocking
    assert following.summary == "Another defect."
    assert item.suggestion == "return []"
    assert "suggestion" not in item.summary and "return []" not in item.summary
    built = build_review([item], RANGES, 1, separate_identity=False)
    [comment] = built.comments
    assert block in comment.body
    assert comment.body.count("```suggestion\nreturn []\n```") == 1
    prompt = review_repair_prompt([item])
    assert block in prompt
    assert prompt.count("return []") == 1


@pytest.mark.parametrize(
    ("location", "path", "start", "end"),
    [
        ("Dockerfile:1", "Dockerfile", 1, 1),
        (".gitignore:2", ".gitignore", 2, 2),
        ("src/Makefile:3-5", "src/Makefile", 3, 5),
        ("src/a_[id]+1-file.py:7", "src/a_[id]+1-file.py", 7, 7),
    ],
)
def test_parse_extensionless_dotfile_and_path_characters(
    location: str, path: str, start: int, end: int
) -> None:
    [item] = parsed(f"## Blocking\n1. High: Bad. `{location}`\n")
    assert (item.path, item.start_line, item.end_line) == (path, start, end)
    built = build_review([item], {path: DiffLines([(1, 10)], [start])}, 1, separate_identity=False)
    [comment] = built.comments
    assert (comment.path, comment.line) == (path, end)


@pytest.mark.parametrize("text", ["at 12:30", "https://x/y:80"])
def test_parse_times_and_urls_are_not_locations(text: str) -> None:
    [item] = parsed(f"## Blocking\n1. High: Bad {text}\n")
    assert item.path is None and item.start_line is None and item.end_line is None
    assert text in item.summary


DIFF = """\
diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1,2 +1,3 @@
 keep
+++ not a header
 more
@@ -20,0 +21,2 @@
+x
+y
diff --git a/gone.py b/gone.py
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
"""


def test_diff_ranges_cover_right_side_hunks_only() -> None:
    assert diff_ranges(DIFF) == {"src/a.py": DiffLines([(1, 3), (21, 22)], [2, 21, 22])}


def finding(**changes: object) -> Finding:
    values: dict[str, object] = {
        "index": 1,
        "severity": "High",
        "summary": "Bad",
        "path": "src/a.py",
        "start_line": 2,
        "end_line": 2,
        "suggestion": None,
        "blocking": True,
    }
    values.update(changes)
    return Finding(**values)  # type: ignore[arg-type]


RANGES = diff_ranges(DIFF)


def test_place_finding_inline_single_line() -> None:
    comment = place_finding(finding(), RANGES, 1)
    assert comment is not None
    assert (comment.path, comment.line, comment.side) == ("src/a.py", 2, "RIGHT")
    assert comment.start_line is None and comment.start_side is None
    assert marked_finding(comment.body) == (1, 1)


def test_place_finding_range_keeps_suggestion_block() -> None:
    comment = place_finding(finding(start_line=1, end_line=3, suggestion="new"), RANGES, 2)
    assert comment is not None
    assert (comment.start_line, comment.line, comment.start_side) == (1, 3, "RIGHT")
    assert "```suggestion\nnew\n```" in comment.body
    assert marked_finding(comment.body) == (2, 1)


def test_place_finding_outside_diff_moves_to_nearest_changed_line_with_note() -> None:
    comment = place_finding(finding(start_line=19, end_line=19, suggestion="new"), RANGES, 1)
    assert comment is not None
    assert comment.line == 21
    assert "`src/a.py:19`" in comment.body and "not part of the diff" in comment.body
    assert "```suggestion" not in comment.body
    # A range crossing two hunks is not one block, so it moves too.
    crossing = place_finding(finding(start_line=3, end_line=21), RANGES, 1)
    assert crossing is not None and crossing.start_line is None and crossing.line == 2


@pytest.mark.parametrize(("reported", "attached", "note"), [(50, 10, True), (12, 12, False)])
def test_place_finding_distinguishes_added_lines_from_context(
    reported: int, attached: int, note: bool
) -> None:
    before = [f"line {number}\n" for number in range(1, 101)]
    after = before.copy()
    after[9] = "changed line 10\n"
    diff = "".join(unified_diff(before, after, fromfile="a/src/a.py", tofile="b/src/a.py", n=3))
    ranges = diff_ranges(diff)
    assert ranges == {"src/a.py": DiffLines([(7, 13)], [10])}
    comment = place_finding(finding(start_line=reported, end_line=reported), ranges, 1)
    assert comment is not None and comment.line == attached
    assert ("attached to the nearest changed line" in comment.body) == note
    assert ("not part of the diff" in comment.body) == note


def test_place_finding_nearest_added_line_ties_use_earlier_same_file_line() -> None:
    ranges = {
        "src/a.py": DiffLines([(7, 13), (27, 33)], [10, 30]),
        "other.py": DiffLines([(20, 20)], [20]),
    }
    comment = place_finding(finding(start_line=20, end_line=20), ranges, 1)
    assert comment is not None and (comment.path, comment.line) == ("src/a.py", 10)


def test_place_finding_outside_deletion_only_hunk_goes_to_body() -> None:
    diff = "--- a/src/a.py\n+++ b/src/a.py\n@@ -9,3 +9,2 @@\n keep\n-removed\n keep\n"
    ranges = diff_ranges(diff)
    assert ranges == {"src/a.py": DiffLines([(9, 10)], [])}
    assert place_finding(finding(start_line=50, end_line=50), ranges, 1) is None
    built = build_review([finding(start_line=50, end_line=50)], ranges, 1, separate_identity=False)
    assert built.comments == () and "src/a.py:50" in built.body


@pytest.mark.parametrize(
    "changes",
    [{"path": None, "start_line": None, "end_line": None}, {"path": "other.py"}],
)
def test_place_finding_without_changed_line_goes_to_body(changes: dict[str, object]) -> None:
    assert place_finding(finding(**changes), RANGES, 1) is None
    built = build_review([finding(**changes)], RANGES, 1, separate_identity=False)
    assert built.comments == ()
    assert "Findings without a changed line" in built.body
    assert "Bad" in built.body


@pytest.mark.parametrize(
    ("separate", "blocking", "event"),
    [
        (False, True, "COMMENT"),
        (False, False, "COMMENT"),
        (True, True, "REQUEST_CHANGES"),
        (True, False, "APPROVE"),
    ],
)
def test_review_event_depends_on_identity_and_blocking(
    separate: bool, blocking: bool, event: str
) -> None:
    built = build_review([finding(blocking=blocking)], RANGES, 1, separate_identity=separate)
    assert built.event == event
    assert len(built.comments) == 1


def test_clean_review_body_states_no_findings() -> None:
    built = build_review([], RANGES, 4, separate_identity=True)
    assert built.event == "APPROVE"
    assert built.body.startswith("No findings.")
    assert "round=4" in built.body and built.comments == ()


def test_validate_decisions() -> None:
    findings = [finding(index=1), finding(index=2, blocking=False)]
    ok = {1: Decision(True), 2: Decision(False, deferred_to=7)}
    assert validate_decisions(findings, ok) == []
    problems = validate_decisions(
        findings,
        {
            1: Decision(False, "  "),
            2: Decision(True, deferred_to=3),
            3: Decision(True),
        },
    )
    assert any("unknown finding 3" in p for p in problems)
    assert any("Rejecting finding 1 requires a reason" in p for p in problems)
    assert any("both accepted and deferred" in p for p in problems)
    assert validate_decisions(findings, {1: Decision(True)}) == ["Finding 2 has no decision"]
    assert validate_decisions([finding()], {1: Decision(False, deferred_to=7)}) == [
        "Blocking finding 1 cannot be deferred"
    ]
    assert validate_decisions([finding(blocking=False)], {1: Decision(False, deferred_to=0)})


def test_review_prompt_carries_contract_scope_ci_and_rounds_log() -> None:
    rounds = [
        RoundEntry(1, finding(), "rejected", "Handled by the caller"),
        RoundEntry(1, finding(index=2, blocking=False), "accepted"),
        RoundEntry(2, finding(index=1, path=None), "deferred", "Deferred to #9"),
    ]
    prompt = review_prompt(
        scope="SCOPE TEXT", ci_result="CI is green.", rounds=rounds, base_ref="refs/heads/develop"
    )
    assert "At most three per round" in prompt and "At most\n  five" in prompt
    assert "`No findings.`" in prompt
    assert "SCOPE TEXT" in prompt and "CI is green." in prompt
    assert "Round 1, finding 1 (rejected)" in prompt and "Reason: Handled by the caller" in prompt
    assert "(accepted)" in prompt and "(deferred)" in prompt
    assert "refs/heads/develop" in prompt
    empty = review_prompt(scope="s", ci_result="c", rounds=[], base_ref="b")
    assert "No earlier rounds." in empty


# ---------------------------------------------------------------- engine


def agent_result(text: str = "Implemented", *, ok: bool = True) -> AgentResult:
    return AgentResult(
        ok=ok,
        final_text=text,
        structured_output=None,
        session_id="session-1",
        usage=UsageRecord(10, None, 4, None, None, 20),
        provider="codex",
        authentication_mode=AuthenticationMode.SUBSCRIPTION_LOGIN,
        error=None if ok else "reviewer failed",
        timed_out=False,
        cancelled=False,
        log_path=None,
        unsupported=None,
    )


def write(content: str) -> ScriptStep:
    def effect(request: AgentRequest) -> None:
        (request.cwd / "allowed.txt").write_text(content)

    return ScriptStep(result=agent_result(), side_effect=effect)


def review(text: str) -> ScriptStep:
    return ScriptStep(result=agent_result(text))


@dataclass
class Posted:
    head: str
    body: str
    comments: tuple[ReviewComment, ...]
    event: str


class FakeReviewGitHub:
    def __init__(self, ticket: Ticket) -> None:
        self.ticket = ticket
        self.repo = "owner/repo"
        self.pr: PullRequest | None = None
        self.posted: list[Posted] = []
        self.reviews_list: list[PostedReview] = []
        self.threads: list[ReviewThread] = []
        self.replies: list[tuple[int, str]] = []
        self.resolved: list[str] = []
        self.resolve_error: Exception | None = None
        self.fail_after_post: Exception | None = None
        self.unresolved_extra = 0

    async def get_ticket(self, number: int) -> Ticket:
        return self.ticket

    async def set_status(self, number: int, status: Status) -> None:
        self.ticket = self.ticket.model_copy(update={"status": status})

    async def comment(self, number: int, body: str) -> None:
        return None

    async def download_attachment(self, url: str, dest: Path) -> Path:
        dest.write_text(SCOPE)
        return dest

    async def find_pr(self, branch: str) -> PullRequest | None:
        return self.pr

    async def create_pr(
        self,
        branch: str,
        base: str,
        title: str,
        body: str,
        assignee: str,
        milestone: str,
        project_title: str,
    ) -> PullRequest:
        self.pr = PullRequest(71, "https://example.test/pr/71")
        return self.pr

    async def checks(self, pr: PullRequest) -> list[CheckResult]:
        return [CheckResult("test", "pass", "https://github.com/owner/repo/actions/runs/1/job/1")]

    async def failed_log_tail(self, run_link: str, max_lines: int = 200) -> str:
        return ""

    async def rerun_failed(self, run_link: str) -> None:
        return None

    async def reviews(self, pr: PullRequest) -> list[PostedReview]:
        return list(self.reviews_list)

    async def post_review(
        self,
        pr: PullRequest,
        head_sha: str,
        body: str,
        comments: Sequence[ReviewComment] = (),
        event: str = "COMMENT",
    ) -> PostedReview:
        self.posted.append(Posted(head_sha, body, tuple(comments), event))
        review = PostedReview(len(self.posted), head_sha, "COMMENTED", body)
        self.reviews_list.append(review)
        for comment in comments:
            number = len(self.threads) + 1
            self.threads.append(
                ReviewThread(f"T{number}", False, (ThreadComment(1000 + number, comment.body),))
            )
        if self.fail_after_post is not None:
            error, self.fail_after_post = self.fail_after_post, None
            raise error
        return review

    async def review_threads(self, pr: PullRequest) -> list[ReviewThread]:
        return [ReviewThread(t.id, t.id in self.resolved, t.comments) for t in self.threads]

    async def unresolved_thread_count(self, pr: PullRequest) -> int:
        return sum(1 for t in self.threads if t.id not in self.resolved) + self.unresolved_extra

    async def reply_to_thread(self, pr: PullRequest, comment_id: int, body: str) -> None:
        self.replies.append((comment_id, body))

    async def resolve_thread(self, thread_id: str) -> None:
        if self.resolve_error is not None:
            raise self.resolve_error
        self.resolved.append(thread_id)


async def git_command(path: Path, *args: str) -> str:
    output = await run_command(["git", *args], cwd=path)
    assert output.returncode == 0, output.stderr
    return output.stdout.strip()


@dataclass
class Harness:
    deps: EngineDeps
    github: FakeReviewGitHub
    origin: Path
    adapter: ScriptedAdapter | None = None

    def script(self, *steps: ScriptStep) -> ScriptedAdapter:
        adapter = ScriptedAdapter(list(steps), name="codex")
        self.adapter = adapter
        self.deps.registry.register("codex", lambda config: adapter)
        return adapter

    def start(self) -> int:
        outcome = asyncio.run(Engine(self.deps).run_to_green(39))
        assert outcome.ci_green
        return outcome.run_id

    def review(self, run_id: int) -> RunOutcome:
        return asyncio.run(Engine(self.deps).run_review(run_id))

    def decide(self, run_id: int, decisions: dict[int, Decision]) -> RunOutcome:
        return asyncio.run(Engine(self.deps).decide(run_id, decisions))

    @property
    def worktree(self) -> Path:
        return worktree_path(self.deps.worktrees_root, self.github.ticket)

    def head(self) -> str:
        return asyncio.run(Git(self.worktree).head_sha())

    def reviewer_requests(self) -> list[AgentRequest]:
        assert self.adapter is not None
        return [r for r in self.adapter.requests if r.role == Role.REVIEWER]


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    async def setup() -> tuple[Path, Path]:
        origin = tmp_path / "origin.git"
        clone = tmp_path / "clone"
        await git_command(tmp_path, "init", "--bare", "--initial-branch=develop", str(origin))
        await git_command(tmp_path, "clone", str(origin), str(clone))
        await git_command(clone, "config", "user.name", "Test Developer")
        await git_command(clone, "config", "user.email", "test@example.test")
        (clone / "README").write_text("Initial")
        await git_command(clone, "add", "README")
        await git_command(clone, "commit", "-m", "Initial")
        await git_command(clone, "push", "origin", "develop")
        return origin, clone

    origin, clone = asyncio.run(setup())
    ticket = Ticket(
        number=39,
        title="Add the review stage",
        issue_type=IssueType.FEATURE,
        size=Size.S,
        status=Status.TO_DO,
        priority=Priority.HIGH,
        milestone="0.1.0",
        assignees=["developer"],
        components=[Component.SOURCE_CODE],
        body_sections={
            "Summary": "Review the pull request",
            "Scope": "Engine",
            "Solution": "[39-solution-2.md](https://github.com/user-attachments/files/scope)",
        },
    )
    github = FakeReviewGitHub(ticket)
    config = FugueConfig()
    config.policy.gate_commands = [[sys.executable, "-c", "print('gate passed')"]]
    with Store(":memory:") as store:
        deps = EngineDeps(
            config,
            store,
            ArtifactStore(tmp_path / "artifacts"),
            github,
            AdapterRegistry(),
            clone,
            tmp_path / "worktrees",
            tmp_path / "state",
        )
        yield Harness(deps, github, origin)


# Scenario 1
@pytest.mark.parametrize(("separate", "event"), [(False, "COMMENT"), (True, "APPROVE")])
def test_clean_round_posts_one_review_at_the_head(
    harness: Harness, separate: bool, event: str
) -> None:
    harness.deps.config.github.reviewer_identity_separate = separate
    harness.script(write(LINES), review("No findings."))
    run_id = harness.start()
    outcome = harness.review(run_id)
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert outcome.head_sha == harness.head()
    [posted] = harness.github.posted
    assert (posted.head, posted.event, posted.comments) == (harness.head(), event, ())
    assert posted.body.startswith("No findings.")
    assert bool(outcome.notes) is (not separate)
    if not separate:
        assert "Approvals are not available" in outcome.notes[0]
    assert harness.deps.store.get_run(run_id).state == RunState.WAITING_FOR_MERGE_APPROVAL
    [request] = harness.reviewer_requests()
    assert request.access == AccessMode.READ_ONLY and request.session_id is None
    assert "Review contract" in request.prompt and "CI is green." in request.prompt
    assert SCOPE in request.prompt
    assert harness.deps.store.usage_summary(run_id)["input_tokens"] == 20


# Scenario 2
@pytest.mark.parametrize(("separate", "event"), [(False, "COMMENT"), (True, "REQUEST_CHANGES")])
def test_blocking_finding_becomes_an_inline_thread_and_waits(
    harness: Harness, separate: bool, event: str
) -> None:
    harness.deps.config.github.reviewer_identity_separate = separate
    harness.script(write(LINES), review(BLOCKING))
    outcome = harness.review(harness.start())
    assert outcome.state == RunState.WAITING_FOR_DECISION
    [found] = outcome.findings
    assert found.blocking and found.path == "allowed.txt"
    [posted] = harness.github.posted
    assert posted.event == event
    [comment] = posted.comments
    assert (comment.path, comment.line, comment.side) == ("allowed.txt", 1, "RIGHT")
    assert "Crash on empty input" in comment.body
    assert harness.deps.store.get_run(outcome.run_id).state == RunState.WAITING_FOR_DECISION


# Scenario 3
def test_range_with_suggestion_posts_start_line_and_block(harness: Harness) -> None:
    harness.script(write(LINES), review(RANGE))
    harness.review(harness.start())
    [comment] = harness.github.posted[0].comments
    assert (comment.start_line, comment.line, comment.start_side) == (2, 3, "RIGHT")
    assert "```suggestion\nTWO\nTHREE\n```" in comment.body


# Scenario 4
def test_line_outside_the_diff_and_unlocated_finding(harness: Harness) -> None:
    text = (
        "## Blocking\n1. High: Far away. `allowed.txt:50`\n2. Medium: General concern.\n"
        "3. Low: Elsewhere. `other.py:3`\n"
    )
    harness.script(write(LINES), review(text))
    harness.review(harness.start())
    [posted] = harness.github.posted
    [comment] = posted.comments
    assert comment.line == 3 and "not part of the diff" in comment.body
    assert "General concern" in posted.body and "Elsewhere" in posted.body


# Scenario 5
@pytest.mark.parametrize("reproduction", ["", "```python\nparse([])\n```\n"])
def test_accept_repairs_pushes_and_reviews_again(harness: Harness, reproduction: str) -> None:
    adapter = harness.script(
        write(LINES), review(BLOCKING + reproduction), write(FIXED), review("No findings.")
    )
    run_id = harness.start()
    first = harness.review(run_id)
    first_head = harness.head()
    outcome = harness.decide(run_id, {1: Decision(True)})
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL
    new_head = harness.head()
    assert new_head != first_head
    branch = branch_name(harness.github.ticket)
    assert asyncio.run(Git(harness.origin).head_sha(branch)) == new_head
    repair = adapter.requests[2]
    assert repair.role == Role.IMPLEMENTER and repair.access == AccessMode.WORKSPACE_WRITE
    assert repair.session_id == "session-1"
    assert "Crash on empty input" in repair.prompt and "allowed.txt:1" in repair.prompt
    if reproduction:
        assert reproduction.rstrip("\n") in repair.prompt
        [comment] = harness.github.posted[0].comments
        assert reproduction.rstrip("\n") in comment.body
    assert [p.head for p in harness.github.posted] == [first_head, new_head]
    assert harness.github.replies[0][0] == 1001
    assert harness.github.replies[0][1].startswith(f"Fixed in {new_head}: ")
    assert harness.github.resolved == ["T1"]
    store = harness.deps.store
    repairs = store._connection.execute(
        "SELECT COUNT(*) FROM stage_attempts WHERE run_id = ? AND stage = 'repair'", (run_id,)
    ).fetchone()
    assert repairs[0] == 1
    assert first.findings and not outcome.findings
    kinds = {
        row[0]
        for row in store._connection.execute(
            "SELECT kind FROM operations WHERE run_id = ?", (run_id,)
        )
    }
    assert {"review", "decision", "review_repair", "thread_reply", "review_post"} <= kinds
    names = [a.name for a in store.list_artifacts(run_id)]
    assert any(name.startswith("review-findings-") for name in names)


# Scenario 5
def test_reject_replies_with_reason_and_feeds_the_next_prompt(harness: Harness) -> None:
    harness.script(write(LINES), review(BLOCKING), review("No findings."))
    run_id = harness.start()
    harness.review(run_id)
    head = harness.head()
    outcome = harness.decide(run_id, {1: Decision(False, "The caller guarantees non-empty input")})
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert harness.head() == head
    assert harness.github.replies == [(1001, "Rejected: The caller guarantees non-empty input")]
    assert harness.github.resolved == ["T1"]
    # A second round at the same head is a separate review.
    assert [p.head for p in harness.github.posted] == [head, head]
    second = harness.reviewer_requests()[1].prompt
    assert "(rejected)" in second
    assert "Reason: The caller guarantees non-empty input" in second


# Scenario 5
def test_invalid_decisions_change_nothing(harness: Harness) -> None:
    harness.script(write(LINES), review(BLOCKING))
    run_id = harness.start()
    harness.review(run_id)
    with pytest.raises(ValueError, match="requires a reason"):
        harness.decide(run_id, {1: Decision(False, None)})
    with pytest.raises(ValueError, match="no decision"):
        harness.decide(run_id, {})
    assert harness.github.replies == [] and harness.github.resolved == []
    assert harness.deps.store.get_run(run_id).state == RunState.WAITING_FOR_DECISION


def test_decide_without_an_open_round_is_rejected(harness: Harness) -> None:
    harness.script(write(LINES), review("No findings."))
    run_id = harness.start()
    with pytest.raises(ValueError, match="waiting for a decision"):
        harness.decide(run_id, {})
    harness.review(run_id)
    with pytest.raises(ValueError, match="waiting for a decision"):
        harness.decide(run_id, {})


# Scenario 5
def test_non_blocking_finding_can_be_deferred_without_another_round(harness: Harness) -> None:
    harness.script(write(LINES), review(NON_BLOCKING))
    run_id = harness.start()
    waiting = harness.review(run_id)
    assert waiting.state == RunState.WAITING_FOR_DECISION
    assert [f.blocking for f in waiting.findings] == [False]
    outcome = harness.decide(run_id, {1: Decision(False, deferred_to=77)})
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert harness.github.replies == [(1001, "Deferred to #77")]
    assert harness.github.resolved == ["T1"]
    assert len(harness.github.posted) == 1 and len(harness.reviewer_requests()) == 1


def test_blocking_finding_cannot_be_deferred(harness: Harness) -> None:
    harness.script(write(LINES), review(BLOCKING))
    run_id = harness.start()
    harness.review(run_id)
    with pytest.raises(ValueError, match="cannot be deferred"):
        harness.decide(run_id, {1: Decision(False, deferred_to=5)})


# Scenario 5
def test_accepted_findings_count_against_the_repair_limit(harness: Harness) -> None:
    harness.deps.config.limits.max_repair_attempts = 1
    harness.script(write(LINES), review(BLOCKING), write(FIXED), review(BLOCKING))
    run_id = harness.start()
    harness.review(run_id)
    waiting = harness.decide(run_id, {1: Decision(True)})
    assert waiting.state == RunState.WAITING_FOR_DECISION
    outcome = harness.decide(run_id, {1: Decision(True)})
    assert outcome.state == RunState.BLOCKED
    assert outcome.problems == ("Repair limit reached",)


def test_accepting_with_no_change_from_the_implementer_blocks(harness: Harness) -> None:
    harness.script(write(LINES), review(BLOCKING), ScriptStep(result=agent_result()))
    run_id = harness.start()
    harness.review(run_id)
    outcome = harness.decide(run_id, {1: Decision(True)})
    assert outcome.state == RunState.BLOCKED
    assert "no changes" in outcome.problems[0]
    assert harness.github.resolved == []


# Scenario 6
def test_resolve_permission_failure_blocks_listing_threads_and_retries(harness: Harness) -> None:
    harness.script(
        write(LINES),
        review(BLOCKING + "2. Medium: Another. `allowed.txt:3`\n"),
        review("No findings."),
    )
    run_id = harness.start()
    harness.review(run_id)
    harness.github.resolve_error = ResolvePermissionError(("gh",), "Resource not accessible")
    decisions = {1: Decision(False, "no"), 2: Decision(False, "no")}
    outcome = harness.decide(run_id, decisions)
    assert outcome.state == RunState.BLOCKED
    assert outcome.problems == ("Cannot resolve review threads (permission denied): T1, T2",)
    assert len(harness.github.replies) == 2
    assert harness.deps.store.get_run(run_id).state == RunState.BLOCKED
    # A retry with a working token resolves the threads and does not reply twice.
    harness.github.resolve_error = None
    retried = harness.decide(run_id, {})
    assert retried.state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert len(harness.github.replies) == 2
    assert harness.github.resolved == ["T1", "T2"]


# Scenario 7
def test_restart_after_posting_does_not_post_or_review_again(harness: Harness) -> None:
    adapter = harness.script(write(LINES), review(BLOCKING))
    run_id = harness.start()
    harness.github.fail_after_post = RuntimeError("stop after posting")
    with pytest.raises(RuntimeError, match="stop after posting"):
        harness.review(run_id)
    assert len(harness.github.posted) == 1
    outcome = harness.review(run_id)
    assert outcome.state == RunState.WAITING_FOR_DECISION
    assert len(harness.github.posted) == 1
    assert len(harness.reviewer_requests()) == 1 and len(adapter.requests) == 2
    # The thread mapping was recovered, so the decision can answer it.
    harness.script(review("No findings."))
    final = harness.decide(run_id, {1: Decision(False, "fine")})
    assert final.state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert harness.github.replies == [(1001, "Rejected: fine")]


def test_calling_run_review_again_while_waiting_returns_the_same_round(harness: Harness) -> None:
    harness.script(write(LINES), review(BLOCKING))
    run_id = harness.start()
    first = harness.review(run_id)
    second = harness.review(run_id)
    assert second.state == RunState.WAITING_FOR_DECISION
    assert second.findings == first.findings
    assert len(harness.github.posted) == 1


# Scenario 8
def test_there_is_no_round_limit(harness: Harness) -> None:
    rounds = 7
    harness.script(write(LINES), *[review(BLOCKING) for _ in range(rounds)], review("No findings."))
    run_id = harness.start()
    outcome = harness.review(run_id)
    for _ in range(rounds):
        assert outcome.state == RunState.WAITING_FOR_DECISION
        outcome = harness.decide(run_id, {1: Decision(False, "still fine")})
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert len(harness.github.posted) == rounds + 1
    assert marked_finding(harness.github.posted[-2].comments[0].body) == (rounds, 1)


def test_unresolved_threads_are_reported_not_hidden(harness: Harness) -> None:
    harness.github.unresolved_extra = 2
    harness.script(write(LINES), review("No findings."))
    outcome = harness.review(harness.start())
    assert outcome.state == RunState.WAITING_FOR_MERGE_APPROVAL
    assert "2 review thread(s) remain unresolved" in outcome.notes


def test_reviewer_failure_stops_without_posting(harness: Harness) -> None:
    harness.script(write(LINES), ScriptStep(result=agent_result("", ok=False)))
    outcome = harness.review(harness.start())
    assert outcome.state == RunState.FAILED
    assert outcome.problems == ("reviewer failed",)
    assert harness.github.posted == []
