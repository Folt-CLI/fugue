"""Attachment transfers and byte-for-byte upload verification."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from fugue.github.client import GitHubClient, GitHubError


@dataclass(frozen=True)
class UploadedAttachment:
    name: str
    href: str
    sha256: str


class AttachmentUnavailable(GitHubError):
    """The attachment extension or its signed-in session is unavailable."""


class AttachmentVerificationError(RuntimeError):
    """An upload could not be verified against its source bytes."""


async def _run(client: GitHubClient, *args: str) -> str:
    try:
        return await client._run(*args)
    except GitHubError as error:
        if any(
            text in error.stderr_tail.casefold()
            for text in ("unknown command", "session", "sign", "login")
        ):
            raise AttachmentUnavailable(error.argv, error.stderr_tail) from error
        raise


async def download_attachment(client: GitHubClient, url: str, dest: Path) -> Path:
    if not url.startswith("https://github.com/user-attachments/"):
        raise ValueError("Expected a GitHub user-attachments URL")
    await _run(client, "attach", "download", url, "--output", str(dest))
    return dest


async def upload_attachments(
    client: GitHubClient, paths: Sequence[Path]
) -> list[UploadedAttachment]:
    if not paths:
        return []
    names = [path.name for path in paths]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate attachment file names are not allowed")
    sources = {path.name: path.read_bytes() for path in paths}
    output = await _run(
        client,
        "attach",
        *(str(path.absolute()) for path in paths),
        "--repo",
        client.repo,
        "--json",
        "href,name",
    )
    items = json.loads(output)
    if not isinstance(items, list) or len(items) != len(paths):
        raise AttachmentVerificationError("Upload response does not match the source files")
    uploaded: list[UploadedAttachment] = []
    with TemporaryDirectory(prefix="fugue-attachments-") as directory:
        for index, item in enumerate(items):
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("href"), str)
                or item["name"] not in sources
            ):
                raise AttachmentVerificationError(
                    "Upload response contains an unexpected attachment"
                )
            name, href = item["name"], item["href"]
            source = sources.pop(name)
            dest = Path(directory) / str(index)
            await download_attachment(client, href, dest)
            if dest.read_bytes() != source:
                raise AttachmentVerificationError(f"Attachment bytes differ from source: {name}")
            uploaded.append(UploadedAttachment(name, href, hashlib.sha256(source).hexdigest()))
    return uploaded
