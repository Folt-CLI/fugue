"""Async Git commands scoped to a repository."""

import re
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

    async def _validate_remote(self, remote: str) -> None:
        if not remote or remote.startswith(("-", "+")):
            raise ValueError("remote must be a configured remote name")
        await self._run("check-ref-format", f"refs/remotes/{remote}/branch")
        await self._run("remote", "get-url", "--", remote)

    async def fetch(self, remote: str = "origin") -> None:
        await self._validate_remote(remote)
        await self._run("fetch", "--", remote)

    async def fast_forward(self, branch: str, remote: str = "origin") -> None:
        await self._validate_branch(branch)
        await self._validate_remote(remote)
        await self._run("show-ref", "--verify", f"refs/heads/{branch}")
        # switch requires a local branch, even when a tag has the same name.
        await self._run("switch", "--no-guess", "--", branch)
        await self._run("merge", "--ff-only", "--", f"refs/remotes/{remote}/{branch}")

    async def _validate_branch(self, branch: str) -> None:
        if branch.startswith(("-", "+")):
            raise ValueError("branch must not begin with '-' or '+'")
        await self._run("check-ref-format", f"refs/heads/{branch}")

    async def create_worktree(self, path: Path, branch: str, base: str) -> None:
        await self._validate_branch(branch)
        base_sha = await self.resolve_ref(base)
        await self._run("worktree", "add", "-b", branch, "--", str(path), base_sha)

    async def remove_worktree(self, path: Path, force: bool = False) -> None:
        options = ("--force",) if force else ()
        await self._run("worktree", "remove", *options, "--", str(path))

    async def resolve_ref(self, name: str) -> str:
        """Resolve commit IDs, pseudo-refs, full refs, or local branch names."""
        if re.fullmatch(r"[0-9a-f]{4,64}", name):
            try:
                return (
                    await self._run(
                        "rev-parse", "--verify", "--quiet", "--end-of-options", f"{name}^{{commit}}"
                    )
                ).strip()
            except GitError:
                # A hex-looking name may instead be a local branch.
                pass
        if name in {"HEAD", "FETCH_HEAD", "ORIG_HEAD"} or name.startswith(
            ("HEAD~", "HEAD^", "refs/")
        ):
            qualified = name
        else:
            root = re.split(r"[~^]|@\{", name, maxsplit=1)[0]
            await self._validate_branch(root)
            qualified = f"refs/heads/{name}"
        return (
            await self._run("rev-parse", "--verify", "--end-of-options", f"{qualified}^{{commit}}")
        ).strip()

    async def head_sha(self, ref: str = "HEAD") -> str:
        return await self.resolve_ref(ref)

    async def current_branch(self) -> str:
        ref = (await self._run("symbolic-ref", "-q", "HEAD")).strip()
        return ref.removeprefix("refs/heads/")

    async def status_porcelain(self) -> list[str]:
        output = await self._run("status", "--porcelain=v1", "--no-renames")
        return output.splitlines()

    async def staged_files(self) -> list[str]:
        output = await self._run("diff", "--cached", "--name-only", "--no-renames", "-z", "--")
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
        base_sha = await self.resolve_ref(base)
        head_sha = await self.resolve_ref(head)
        return await self._run(
            "diff", "--no-ext-diff", "--no-color", "--no-renames", base_sha, head_sha, "--"
        )

    async def commit(self, message: CommitMessage) -> str:
        await self._run("commit", "--cleanup=verbatim", "-F", "-", stdin=message.render())
        return await self.head_sha()

    async def push(self, branch: str, remote: str = "origin") -> None:
        await self._validate_branch(branch)
        await self._validate_remote(remote)
        await self._run("push", "--", remote, f"refs/heads/{branch}:refs/heads/{branch}")
