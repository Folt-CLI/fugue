import asyncio
import subprocess
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from fugue.gitops import CommitMessage, Git, GitError, validate_message, validate_staging


def git_command(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key, value in {
        "GIT_AUTHOR_NAME": "Fugue Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Fugue Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "commit.gpgsign",
        "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "core.hooksPath",
        "GIT_CONFIG_VALUE_1": "/dev/null",
    }.items():
        monkeypatch.setenv(key, value)
    git_command(tmp_path, "init", "--bare", "--initial-branch=develop", "origin")
    git_command(tmp_path, "clone", str(tmp_path / "origin"), "clone")
    clone = tmp_path / "clone"
    (clone / "initial.txt").write_text("initial\n")
    git_command(clone, "add", "initial.txt")
    git_command(clone, "commit", "-m", "Initial fixture")
    git_command(clone, "push", "origin", "develop")
    return clone


def message() -> CommitMessage:
    return CommitMessage("Feature", 14, "Add Git operations", "Support repository changes.")


def test_worktree_create_remove(repo: Path) -> None:
    async def scenario() -> None:
        git = Git(repo)
        path = repo.parent / "worktree with spaces"
        await git.create_worktree(path, "feature/worktree", "HEAD")
        assert (path / "initial.txt").read_text() == "initial\n"
        assert await Git(path).current_branch() == "feature/worktree"
        assert await Git(path).head_sha() == await git.head_sha()
        (path / "untracked").write_text("dirty")
        with pytest.raises(GitError):
            await git.remove_worktree(path)
        await git.remove_worktree(path, force=True)
        assert not path.exists()
        await git.create_worktree(path, "feature/clean", "develop")
        await git.remove_worktree(path)
        assert not path.exists()

    asyncio.run(scenario())


def test_fetch_fast_forward_and_divergence(repo: Path) -> None:
    peer = repo.parent / "peer"
    git_command(repo.parent, "clone", str(repo.parent / "origin"), str(peer))

    def advance_peer(filename: str) -> str:
        (peer / filename).write_text(filename)
        git_command(peer, "add", filename)
        git_command(peer, "commit", "-m", "Advance remote")
        git_command(peer, "push", "origin", "develop")
        return git_command(peer, "rev-parse", "HEAD").strip()

    async def scenario() -> None:
        git = Git(repo)
        original = await git.head_sha()
        remote_head = advance_peer("remote.txt")
        await git.fetch()
        assert await git.head_sha() == original
        await git.fast_forward("develop")
        assert await git.head_sha() == remote_head
        (repo / "local.txt").write_text("local")
        await git.stage(["local.txt"])
        local_head = await git.commit(message())
        advance_peer("other.txt")
        await git.fetch()
        with pytest.raises(GitError) as exc_info:
            await git.fast_forward("develop")
        assert "--ff-only" in exc_info.value.argv
        assert await git.head_sha() == local_head
        assert await git.status_porcelain() == []

    asyncio.run(scenario())


def test_stage_commit_diff_and_push(repo: Path) -> None:
    async def scenario() -> None:
        git = Git(repo)
        base = await git.head_sha("develop")
        assert await git.current_branch() == "develop"
        assert await git.status_porcelain() == []
        assert await git.staged_files() == []
        await git.stage([])
        names = ["space name.txt", "line\nbreak.txt", "unicode-雪.txt", "-option.txt"]
        for name in names:
            (repo / name).write_text("new content\n")
        assert '?? "space name.txt"' in await git.status_porcelain()
        await git.stage([*names[:-1], "./-option.txt"])
        assert set(await git.staged_files()) == set(names)
        commit_message = replace(message(), changes=("Add commands: support Git", "Add tests"))
        sha = await git.commit(commit_message)
        assert len(sha) == 40
        assert sha == await git.head_sha()
        assert git_command(repo, "log", "-1", "--format=%B") == commit_message.render() + "\n"
        assert await git.staged_files() == []
        assert await git.status_porcelain() == []
        diff = await git.diff(base, sha)
        assert "+new content" in diff
        assert "space name.txt" in diff
        await git.push("develop")
        assert git_command(repo.parent / "origin", "rev-parse", "develop").strip() == sha

    asyncio.run(scenario())


@pytest.mark.parametrize("path", [".", "./", "", "-A", "--all", "*", "*.py", ":(all)", "../"])
def test_stage_rejects_broad_inputs(repo: Path, path: str) -> None:
    (repo / "new.txt").write_text("untracked")
    with pytest.raises(ValueError, match="explicit path"):
        asyncio.run(Git(repo).stage(["new.txt", path]))
    assert asyncio.run(Git(repo).staged_files()) == []


def test_git_error_details(repo: Path) -> None:
    with pytest.raises(GitError) as exc_info:
        asyncio.run(Git(repo).head_sha("missing-ref"))
    error = exc_info.value
    assert error.argv == ("git", "rev-parse", "--verify", "--end-of-options", "missing-ref")
    assert error.returncode == 128
    assert "fatal:" in error.stderr
    assert error.stderr in str(error)
    assert "128" in str(error)
    bounded = GitError(("git", "fetch"), 1, "x" * 5000 + "tail")
    assert len(bounded.stderr) == 4096
    assert bounded.stderr.endswith("tail")


def test_commit_failure(repo: Path) -> None:
    before = asyncio.run(Git(repo).head_sha())
    with pytest.raises(GitError):
        asyncio.run(Git(repo).commit(message()))
    assert asyncio.run(Git(repo).head_sha()) == before


def test_push_does_not_force(repo: Path) -> None:
    git_command(repo, "push", "origin", "develop:refs/heads/unrelated")
    git_command(repo, "checkout", "--orphan", "unrelated")
    git_command(repo, "commit", "-m", "Unrelated history")
    with pytest.raises(GitError) as exc_info:
        asyncio.run(Git(repo).push("unrelated", "origin"))
    assert "--force" not in exc_info.value.argv


def test_message_render_and_immutable() -> None:
    m = message()
    assert m.render() == (
        "Feature[#14]: Add Git operations\n\n**Summary**\nSupport repository changes."
    )
    assert replace(m, changes=("First change", "Second change")).render() == (
        m.render() + "\n\n**Changes**\n- First change\n- Second change"
    )
    with pytest.raises(FrozenInstanceError):
        m.number = 1  # type: ignore[misc]
    assert validate_message(m, {"Feature"}) == []


@pytest.mark.parametrize(
    ("field", "value", "problem"),
    [
        ("issue_type", "Unknown", "issue type"),
        ("explanation", "A" * 72, "exceeds"),
        ("explanation", "lowercase", "capitalised"),
        ("explanation", "", "capitalised"),
        ("explanation", "Trailing period.", "end with"),
        ("summary", " \n", "empty"),
        ("summary", "Co-Authored-By: Someone", "Attribution"),
        ("explanation", "Generated With tool", "Attribution"),
        ("issue_type", "Signed-Off-By", "Attribution"),
    ],
)
def test_message_validation(field: str, value: str, problem: str) -> None:
    m = message()
    if field == "issue_type":
        m = replace(m, issue_type=value)
    elif field == "explanation":
        m = replace(m, explanation=value)
    else:
        m = replace(m, summary=value)
    assert any(problem in error for error in validate_message(m, {"Feature"}))


def test_message_validation_changes_and_subject_boundary() -> None:
    m = replace(message(), changes=("Fine", "sIgNeD-oFf-By: Someone"))
    assert any("Attribution" in error for error in validate_message(m, {"Feature"}))
    length = len(message().render().splitlines()[0])
    assert validate_message(message(), {"Feature"}, max_subject=length) == []
    assert validate_message(message(), {"Feature"}, max_subject=length - 1) == [
        f"Subject exceeds {length - 1} characters"
    ]
    errors = validate_message(CommitMessage("Bad", 1, "bad.", ""), {"Feature"}, max_subject=1)
    assert len(errors) == 5


def test_staging_validation() -> None:
    assert validate_staging([], []) == []
    assert validate_staging(["src/a.py"], []) == ["src/a.py"]
    assert validate_staging(
        ["src/a.py", "src/nested/b.py", "docs/readme.md", "exact.txt", "src2/a.py", "other"],
        ["src/*.py", "docs/", "exact.txt"],
    ) == ["src2/a.py", "other"]
    assert validate_staging(["docs", "docs/a/b", "docs-other/file"], ["docs/"]) == [
        "docs",
        "docs-other/file",
    ]
    assert validate_staging(["A.py", "a.py"], ["a.py"]) == ["A.py"]


def test_diff_preserves_large_output(repo: Path) -> None:
    async def scenario() -> None:
        git = Git(repo)
        base = await git.head_sha()
        content = "x" * 100_000 + "tail"
        (repo / "large.txt").write_text(content + "\n")
        await git.stage(["large.txt"])
        head = await git.commit(message())
        assert "+" + content + "\n" in await git.diff(base, head)

    asyncio.run(scenario())
