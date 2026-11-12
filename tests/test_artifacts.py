import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from fugue.artifacts import ArtifactConflict, ArtifactError, ArtifactStore


def test_write_read_round_trip(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    ref = store.write(13, "findings.txt", "Findings\n")

    assert ref.name == "findings.txt"
    assert ref.path == tmp_path / "artifacts" / "run-13" / "findings.txt"
    assert store.read_text(ref) == "Findings\n"
    assert store.open_ref(13, "findings.txt") == ref
    assert store.verify(ref)
    with pytest.raises(FrozenInstanceError):
        ref.name = "changed.txt"  # type: ignore[misc]


def test_binary_checksum_and_size(tmp_path: Path) -> None:
    data = b"\x00\xff\x80binary\n"
    ref = ArtifactStore(tmp_path).write(1, "output.bin", data)

    assert ref.path.read_bytes() == data
    assert ref.sha256 == hashlib.sha256(data).hexdigest()
    assert ref.size_bytes == len(data)


def test_identical_rewrite_preserves_file(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.write(1, "log.txt", "same")
    before = ref.path.stat()

    assert store.write(1, "log.txt", b"same") == ref
    after = ref.path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert list(ref.path.parent.iterdir()) == [ref.path]


def test_conflict_retains_existing_content(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.write(1, "log.txt", "original")

    with pytest.raises(ArtifactConflict):
        store.write(1, "log.txt", "different")

    assert store.read_text(ref) == "original"
    assert store.verify(ref)


@pytest.mark.parametrize("name", ["", ".", "..", "../log", "a/b", "a\\b", "\0"])
def test_invalid_names(tmp_path: Path, name: str) -> None:
    store = ArtifactStore(tmp_path)
    with pytest.raises(ArtifactError):
        store.write(1, name, "content")
    with pytest.raises(ArtifactError):
        store.open_ref(1, name)
    assert not list(tmp_path.iterdir())


def test_verify_detects_tampering_and_missing_file(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.write(1, "log.txt", "original")
    ref.path.write_bytes(b"tampered")
    assert not store.verify(ref)
    ref.path.unlink()
    assert not store.verify(ref)


def test_failed_replace_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_replace(src: Path, dst: Path) -> None:
        assert src.read_bytes() == b"content"
        assert not dst.exists()
        raise OSError("replace failed")

    monkeypatch.setattr("fugue.artifacts.store.os.replace", fail_replace)
    store = ArtifactStore(tmp_path)
    with pytest.raises(ArtifactError, match="Cannot write artifact") as exc_info:
        store.write(1, "log.txt", "content")

    assert isinstance(exc_info.value.__cause__, OSError)
    assert list((tmp_path / "run-1").iterdir()) == []
    assert store.open_ref(1, "log.txt") is None


def test_text_is_utf8(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    text = "Наоди café 日本語"
    ref = store.write(1, "findings.txt", text)

    assert ref.path.read_bytes() == text.encode("utf-8")
    assert ref.size_bytes == len(text.encode("utf-8"))
    assert store.read_text(ref) == text


def test_open_missing_ref_does_not_create_directories(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    assert ArtifactStore(root).open_ref(1, "log.txt") is None
    assert not root.exists()


def test_runs_have_separate_artifacts(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    first = store.write(1, "log.txt", "first")
    second = store.write(2, "log.txt", "second")

    assert store.read_text(first) == "first"
    assert store.read_text(second) == "second"


def test_read_text_errors(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ref = store.write(1, "output.bin", b"\xff")
    with pytest.raises(ArtifactError, match="Cannot read artifact as UTF-8"):
        store.read_text(ref)
    ref.path.unlink()
    with pytest.raises(ArtifactError, match="Cannot read artifact as UTF-8"):
        store.read_text(ref)
