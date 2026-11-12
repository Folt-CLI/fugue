"""Async Git commands scoped to a repository."""

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from fugue.gitops.commit import CommitMessage
from fugue.process import run_command


class GitError(RuntimeError):
    """A failed Git command and its bounded stderr tail."""

    def __init__(self, argv: Sequence[str], returncode: int | None, stderr: str) -> None:
        self.argv = tuple(argv)
        self.returncode = returncode
        self.stderr = stderr[-4096:]
        super().__init__(f"Git command {self.argv!r} failed ({returncode}): {self.stderr}")


class Git:
    def __init__(self, repo: Path) -> None:
        self.repo = repo

    async def _run(self, *args: str, stdin: str | None = None) -> str:
        output: list[str] = []

        async def capture(line: str) -> None:
            output.append(line)

        result = await run_command(
            ("git", *args),
            cwd=self.repo,
            stdin=stdin,
            on_stdout_line=capture,
            max_line_bytes=None,
        )
        if result.returncode != 0:
            raise GitError(result.argv, result.returncode, result.stderr)
        return "".join(output)

    async def fetch(self, remote: str = "origin") -> None:
        await self._run("fetch", "--", remote)

    async def fast_forward(self, branch: str, remote: str = "origin") -> None:
        if branch.startswith("-"):
            raise ValueError("branch must not be an option")
        await self._run("checkout", "--no-guess", branch, "--")
        await self._run("merge", "--ff-only", "--", f"{remote}/{branch}")

    async def create_worktree(self, path: Path, branch: str, base: str) -> None:
        await self._run("worktree", "add", "-b", branch, "--", str(path), base)

    async def remove_worktree(self, path: Path, force: bool = False) -> None:
        options = ("--force",) if force else ()
        await self._run("worktree", "remove", *options, "--", str(path))

    async def head_sha(self, ref: str = "HEAD") -> str:
        return (await self._run("rev-parse", "--verify", "--end-of-options", ref)).strip()

    async def current_branch(self) -> str:
        return (await self._run("symbolic-ref", "--short", "HEAD")).strip()

    async def status_porcelain(self) -> list[str]:
        output = await self._run("status", "--porcelain=v1")
        return output.splitlines()

    async def staged_files(self) -> list[str]:
        output = await self._run("diff", "--cached", "--name-only", "-z", "--")
        return output.removesuffix("\0").split("\0") if output else []

    async def stage(self, paths: Sequence[str]) -> None:
        for path in paths:
            parts = PurePosixPath(path).parts
            if (
                not parts
                or path.startswith(("-", ":"))
                or ".." in parts
                or any(character in path for character in "*?[")
            ):
                raise ValueError(f"An explicit path is required: {path!r}")
        if paths:
            await self._run("--literal-pathspecs", "add", "--", *paths)

    async def diff(self, base: str, head: str) -> str:
        return await self._run("diff", "--no-ext-diff", "--no-color", base, head, "--")

    async def commit(self, message: CommitMessage) -> str:
        await self._run("commit", "--cleanup=verbatim", "-F", "-", stdin=message.render())
        return await self.head_sha()

    async def push(self, branch: str, remote: str = "origin") -> None:
        await self._run("push", "--", remote, f"{branch}:refs/heads/{branch}")
