"""Attachment transfers use only the fixture-driven fake gh child process."""

import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from fugue.github import (
    AttachmentUnavailable,
    AttachmentVerificationError,
    GitHubClient,
    GitHubError,
    UploadedAttachment,
)

URL = "https://github.com/user-attachments/files/123/example.bin"
Fake = Callable[..., tuple[GitHubClient, Callable[[], list[dict[str, Any]]]]]


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fake:
    fixture = tmp_path / "fixture.json"
    record = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_GH_FIXTURE", str(fixture))
    monkeypatch.setenv("FAKE_GH_RECORD", str(record))

    def setup(
        responses: list[tuple[list[str], dict[str, Any]]], *, strip_tokens: bool = True
    ) -> tuple[GitHubClient, Callable[[], list[dict[str, Any]]]]:
        fixture.write_text(json.dumps({json.dumps(key): value for key, value in responses}))
        record.write_text("")
        client = GitHubClient(
            "example/fugue",
            "example",
            2,
            strip_tokens=strip_tokens,
            gh_executable=[sys.executable, str(Path(__file__).with_name("fake_gh.py"))],
        )

        def calls() -> list[dict[str, Any]]:
            return [json.loads(line) for line in record.read_text().splitlines()]

        return client, calls

    return setup


def test_download(fake: Fake, tmp_path: Path) -> None:
    source = tmp_path / "fixture.bin"
    source.write_bytes(b"\x00\xffbinary\r\n")
    dest = tmp_path / "download.bin"
    client, calls = fake([(["attach", "download", URL], {"download_file": str(source)})])
    assert asyncio.run(client.download_attachment(URL, dest)) == dest
    assert dest.read_bytes() == source.read_bytes()
    assert calls()[0]["argv"] == ["attach", "download", URL, "--output", str(dest)]


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/user-attachments/a",
        "https://github.com/user-attachments",
        "https://github.com.evil/user-attachments/a",
        "https://example.com/a",
        "https://github.com/user-attachments-other/a",
        " https://github.com/user-attachments/a",
    ],
)
def test_bad_url(fake: Fake, tmp_path: Path, url: str) -> None:
    client, calls = fake([])
    with pytest.raises(ValueError, match="user-attachments"):
        asyncio.run(client.download_attachment(url, tmp_path / "out"))
    assert calls() == []


def test_upload_verified_round_trip(fake: Fake, tmp_path: Path) -> None:
    first = tmp_path / "first file.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"\x00\xfffirst\r\n")
    second.write_bytes(b"")
    second_url = URL + "-second"
    # Return files in a different order to prove sources are matched by name.
    items = [{"name": second.name, "href": second_url}, {"name": first.name, "href": URL}]
    client, calls = fake(
        [
            (["attach"], {"stdout": json.dumps(items)}),
            (["attach", "download", URL], {"download_file": str(first)}),
            (["attach", "download", second_url], {"download_file": str(second)}),
        ]
    )
    assert asyncio.run(client.upload_attachments([first, second])) == [
        UploadedAttachment(second.name, second_url, hashlib.sha256(b"").hexdigest()),
        UploadedAttachment(first.name, URL, hashlib.sha256(first.read_bytes()).hexdigest()),
    ]
    assert calls()[0]["argv"] == [
        "attach",
        str(first),
        str(second),
        "--repo",
        "example/fugue",
        "--json",
        "href,name",
    ]
    assert len(calls()) == 3
    for call in calls()[1:]:
        assert call["argv"][:2] == ["attach", "download"]
        assert not Path(call["argv"][-1]).exists()


def test_symlink_upload_preserves_supplied_name(
    fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = tmp_path / "content.txt"
    content.write_bytes(b"report content")
    report = tmp_path / "report.md"
    report.symlink_to(content)
    client, calls = fake(
        [
            (
                ["attach", str(report)],
                {"stdout": json.dumps([{"name": report.name, "href": URL}])},
            ),
            (["attach", "download", URL], {"download_file": str(content)}),
        ]
    )
    monkeypatch.chdir(tmp_path)
    assert asyncio.run(client.upload_attachments([Path("report.md")])) == [
        UploadedAttachment("report.md", URL, hashlib.sha256(content.read_bytes()).hexdigest())
    ]
    assert calls()[0]["argv"][1] == str(report)


@pytest.mark.parametrize("same_path", [False, True])
def test_duplicate_upload_names_rejected(fake: Fake, tmp_path: Path, same_path: bool) -> None:
    first = tmp_path / "report.md"
    first.write_bytes(b"first")
    second = first if same_path else tmp_path / "other" / "report.md"
    if not same_path:
        second.parent.mkdir()
        second.write_bytes(b"second")
    client, calls = fake([])
    with pytest.raises(ValueError, match="Duplicate attachment file names"):
        asyncio.run(client.upload_attachments([first, second]))
    assert calls() == []


def test_mismatch_raises_and_cleans_temp_directory(fake: Fake, tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"expected\r\n")
    wrong = tmp_path / "wrong.bin"
    wrong.write_bytes(b"expected\n")
    client, calls = fake(
        [
            (["attach"], {"stdout": json.dumps([{"name": source.name, "href": URL}])}),
            (["attach", "download"], {"download_file": str(wrong)}),
        ]
    )
    with pytest.raises(AttachmentVerificationError, match=source.name):
        asyncio.run(client.upload_attachments([source]))
    assert not Path(calls()[1]["argv"][-1]).parent.exists()


@pytest.mark.parametrize(
    "stderr",
    [
        'UNKNOWN COMMAND "attach"',
        "No SESSION available",
        "Please SIGN in",
        "Run LOGIN first",
    ],
)
@pytest.mark.parametrize("upload", [False, True])
def test_unavailable(fake: Fake, tmp_path: Path, stderr: str, upload: bool) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    client, _ = fake([(["attach"], {"exit_code": 1, "stderr": stderr})])
    with pytest.raises(AttachmentUnavailable) as error:
        if upload:
            asyncio.run(client.upload_attachments([source]))
        else:
            asyncio.run(client.download_attachment(URL, tmp_path / "out"))
    assert isinstance(error.value, GitHubError)
    assert error.value.stderr_tail == stderr


@pytest.mark.parametrize("strip_tokens", [True, False])
def test_token_handling(
    fake: Fake, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strip_tokens: bool
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-one")
    monkeypatch.setenv("GH_TOKEN", "secret-two")
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    expected_env = {"GITHUB_TOKEN": not strip_tokens, "GH_TOKEN": not strip_tokens}
    client, calls = fake(
        [
            (
                ["attach"],
                {
                    "stdout": json.dumps([{"name": source.name, "href": URL}]),
                    "expect_env": expected_env,
                },
            ),
            (["attach", "download"], {"download_file": str(source), "expect_env": expected_env}),
        ],
        strip_tokens=strip_tokens,
    )
    asyncio.run(client.upload_attachments([source]))
    asyncio.run(client.download_attachment(URL, tmp_path / "out"))
    assert len(calls()) == 3
    assert os.environ["GH_TOKEN"] == "secret-two"


def test_empty_upload(fake: Fake) -> None:
    client, calls = fake([])
    assert asyncio.run(client.upload_attachments([])) == []
    assert calls() == []


@pytest.mark.parametrize("items", [[], [{"name": "other", "href": URL}], [{}]])
def test_incomplete_upload_response(fake: Fake, tmp_path: Path, items: list[object]) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    client, calls = fake([(["attach"], {"stdout": json.dumps(items)})])
    with pytest.raises(AttachmentVerificationError):
        asyncio.run(client.upload_attachments([source]))
    assert len(calls()) == 1


def test_download_failure_during_verification(fake: Fake, tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    client, _ = fake(
        [
            (["attach"], {"stdout": json.dumps([{"name": source.name, "href": URL}])}),
            (["attach", "download"], {"exit_code": 1, "stderr": "No SESSION"}),
        ]
    )
    with pytest.raises(AttachmentUnavailable):
        asyncio.run(client.upload_attachments([source]))


def test_other_errors_remain_github_errors(fake: Fake, tmp_path: Path) -> None:
    client, _ = fake([(["attach"], {"exit_code": 1, "stderr": "network failure"})])
    with pytest.raises(GitHubError) as error:
        asyncio.run(client.download_attachment(URL, tmp_path / "out"))
    assert type(error.value) is GitHubError
