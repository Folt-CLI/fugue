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
        base_sha = await self._resolve_ref(base)
        await self._run("worktree", "add", "-b", branch, "--", str(path), base_sha)

    async def remove_worktree(self, path: Path, force: bool = False) -> None:
        options = ("--force",) if force else ()
        await self._run("worktree", "remove", *options, "--", str(path))

    async def _resolve_ref(self, ref: str) -> str:
        """Resolve to a commit ID; ambiguous short refs require a full ref name."""
        root = re.split(r"[~^]|@\{", ref, maxsplit=1)[0]
        suffix = ref[len(root) :]
        if not root or root.startswith(("-", "+")):
            raise ValueError("ref must name a commit")
        if root == "HEAD" or re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", root):
            qualified = root
        elif root.startswith("refs/"):
            await self._run("check-ref-format", root)
            qualified = root
        else:
            await self._run("check-ref-format", f"refs/heads/{root}")
            refs = (await self._run("for-each-ref", "--format=%(refname)")).splitlines()
            candidates = {
                f"refs/{root}",
                f"refs/heads/{root}",
                f"refs/tags/{root}",
                f"refs/remotes/{root}",
                f"refs/remotes/{root}/HEAD",
            }
            matches = [name for name in refs if name in candidates]
            if len(matches) > 1:
                raise ValueError(f"Ambiguous ref {root!r}; use a fully qualified ref")
            qualified = matches[0] if matches else f"refs/heads/{root}"
        return (
            await self._run(
                "rev-parse", "--verify", "--end-of-options", f"{qualified}{suffix}^{{commit}}"
            )
        ).strip()

    async def head_sha(self, ref: str = "HEAD") -> str:
        return await self._resolve_ref(ref)

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
        base_sha = await self._resolve_ref(base)
        head_sha = await self._resolve_ref(head)
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
