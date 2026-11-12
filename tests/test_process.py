import asyncio
import os
import signal
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from fugue.process import CommandResult, run_command


def _python(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Orphaned descendants can remain zombies until the system reaps them.
    stat = Path(f"/proc/{pid}/stat")
    try:
        return stat.read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def test_success() -> None:
    argv = _python("import sys; print('hello'); print('warning', file=sys.stderr)")
    result = asyncio.run(run_command(argv))
    assert isinstance(result, CommandResult)
    assert result.argv == argv
    assert result.returncode == 0
    assert result.stdout == "hello\n"
    assert result.stderr == "warning\n"
    assert not result.timed_out
    assert not result.cancelled
    assert not result.stdout_truncated
    assert not result.stderr_truncated
    assert result.full_output_path is None
    assert result.duration_seconds >= 0
    with pytest.raises(FrozenInstanceError):
        result.returncode = 1  # type: ignore[misc]


def test_nonzero_returncode() -> None:
    result = asyncio.run(run_command(_python("raise SystemExit(7)")))
    assert result.returncode == 7
    assert result.stdout == result.stderr == ""


def test_cwd_and_env_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FUGUE_INHERITED", "inherited")
    monkeypatch.setenv("FUGUE_OVERLAY", "old")
    result = asyncio.run(
        run_command(
            _python(
                "import os; print(os.getcwd()); "
                "print(os.environ['FUGUE_INHERITED']); print(os.environ['FUGUE_OVERLAY'])"
            ),
            cwd=tmp_path,
            env={"FUGUE_OVERLAY": "new"},
        )
    )
    assert result.stdout.splitlines() == [str(tmp_path), "inherited", "new"]
    assert os.environ["FUGUE_OVERLAY"] == "old"


@pytest.mark.parametrize("stdin", [None, "", "hello\nworld \u2603"])
def test_stdin(stdin: str | None) -> None:
    result = asyncio.run(
        run_command(_python("import sys; sys.stdout.write(sys.stdin.read())"), stdin=stdin)
    )
    assert result.stdout == (stdin or "")


def test_child_closes_stdin_early() -> None:
    result = asyncio.run(run_command(_python("pass"), stdin="x" * 1_000_000))
    assert result.returncode == 0


def test_timeout() -> None:
    result = asyncio.run(
        run_command(
            _python("import os, time; print(os.getpid(), flush=True); time.sleep(60)"),
            timeout=0.5,
        )
    )
    assert result.timed_out
    assert not result.cancelled
    assert result.returncode == -signal.SIGTERM
    assert not _is_running(int(result.stdout))


def test_timeout_kills_grandchild(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    grandchild = (
        "import os, signal, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    code = (
        "import os, subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
        "print(os.getpid(), flush=True); time.sleep(60)"
    )
    result = asyncio.run(run_command(_python(code), timeout=1.0))
    assert result.timed_out
    assert not _is_running(int(result.stdout))
    assert not _is_running(int(ready.read_text()))


def test_timeout_escalates_to_sigkill() -> None:
    result = asyncio.run(
        run_command(
            _python(
                "import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "print(os.getpid(), flush=True); time.sleep(60)"
            ),
            timeout=0.5,
        )
    )
    assert result.timed_out
    assert result.returncode == -signal.SIGKILL
    assert not _is_running(int(result.stdout))


def test_cancellation() -> None:
    async def scenario() -> None:
        ready = asyncio.Event()
        pids: list[int] = []

        async def line_received(line: str) -> None:
            pids.append(int(line))
            ready.set()

        task = asyncio.create_task(
            run_command(
                _python("import os, time; print(os.getpid(), flush=True); time.sleep(60)"),
                on_stdout_line=line_received,
            )
        )
        try:
            await asyncio.wait_for(ready.wait(), 5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert len(pids) == 1
        assert not _is_running(pids[0])

    asyncio.run(scenario())


def test_cancellation_with_blocked_stdin() -> None:
    async def scenario() -> None:
        ready = asyncio.Event()
        pids: list[int] = []

        async def receive(line: str) -> None:
            pids.append(int(line))
            ready.set()

        task = asyncio.create_task(
            run_command(
                _python("import os, time; print(os.getpid(), flush=True); time.sleep(60)"),
                stdin="x" * (1024 * 1024),
                on_stdout_line=receive,
            )
        )
        try:
            await asyncio.wait_for(ready.wait(), 3)
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=3)
            assert task in done, "cancellation blocked on unread stdin"
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not _is_running(pids[0])
        finally:
            for pid in pids:
                if _is_running(pid):
                    os.kill(pid, signal.SIGKILL)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_timeout_with_blocked_callback() -> None:
    async def scenario() -> None:
        ready = asyncio.Event()
        blocked = asyncio.Event()
        pids: list[int] = []

        async def receive(line: str) -> None:
            pids.append(int(line))
            ready.set()
            await blocked.wait()

        task = asyncio.create_task(
            run_command(
                _python("import os, time; print(os.getpid(), flush=True); time.sleep(60)"),
                timeout=0.5,
                on_stdout_line=receive,
            )
        )
        try:
            await asyncio.wait_for(ready.wait(), 3)
            done, _ = await asyncio.wait({task}, timeout=3)
            assert task in done, "timeout blocked on the stdout callback"
            result = await task
            assert result.timed_out
            assert result.returncode == -signal.SIGTERM
            assert result.stdout == f"{pids[0]}\n"
            assert not _is_running(pids[0])
        finally:
            blocked.set()
            for pid in pids:
                if _is_running(pid):
                    os.kill(pid, signal.SIGKILL)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_truncated_output_and_spool(tmp_path: Path) -> None:
    stdout = "HEAD" + "x" * 100_000 + "TAIL"
    stderr = "START" + "y" * 100_000 + "END"
    result = asyncio.run(
        run_command(
            _python(
                "import sys; sys.stdout.write('HEAD' + 'x' * 100000 + 'TAIL'); "
                "sys.stderr.write('START' + 'y' * 100000 + 'END')"
            ),
            max_output_bytes=16,
            spool_dir=tmp_path / "spool",
        )
    )
    assert result.returncode == 0
    assert result.stdout == "HEADxxxx\n[... truncated 99992 bytes ...]\nxxxxTAIL"
    assert result.stderr == "STARTyyy\n[... truncated 99992 bytes ...]\nyyyyyEND"
    assert result.stdout_truncated
    assert result.stderr_truncated
    assert result.full_output_path is not None
    assert result.full_output_path.parent == tmp_path / "spool"
    content = result.full_output_path.read_bytes()
    # Chunks from the two streams may interleave, but no bytes may be lost.
    assert len(content) == len(stdout) + len(stderr)
    assert content.count(b"x") == 100_000
    assert content.count(b"y") == 100_000
    for boundary in (b"HEAD", b"TAIL", b"START", b"END"):
        assert content.count(boundary) == 1


def test_single_stream_spool_is_exact(tmp_path: Path) -> None:
    payload = "hello \u2603\n" * 30_000
    result = asyncio.run(
        run_command(
            _python("import sys; sys.stdout.write('hello \\u2603\\n' * 30000)"),
            max_output_bytes=31,
            spool_dir=tmp_path,
        )
    )
    assert result.stdout_truncated
    assert not result.stderr_truncated
    assert result.full_output_path is not None
    assert result.full_output_path.read_bytes() == payload.encode()


@pytest.mark.parametrize("cap", [0, 1, 3, 4, 5, 8])
def test_capture_boundaries(cap: int) -> None:
    result = asyncio.run(
        run_command(_python("import sys; sys.stdout.write('abcd')"), max_output_bytes=cap)
    )
    assert result.stdout_truncated == (cap < 4)
    assert result.full_output_path is None
    if cap < 4:
        first = "abcd"[: cap // 2]
        last_size = cap - cap // 2
        last = "abcd"[-last_size:] if last_size else ""
        assert result.stdout == f"{first}\n[... truncated {4 - cap} bytes ...]\n{last}"
    else:
        assert result.stdout == "abcd"


def test_no_spool_below_limit(tmp_path: Path) -> None:
    result = asyncio.run(run_command(_python("print('small')"), spool_dir=tmp_path))
    assert result.full_output_path is None
    assert list(tmp_path.iterdir()) == []


def test_line_callback() -> None:
    lines: list[str] = []

    async def receive(line: str) -> None:
        lines.append(line)
        await asyncio.sleep(0)

    result = asyncio.run(
        run_command(
            _python(
                "import sys; sys.stdout.write('first\\n\\n' + '\\u2603' * 70000 + '\\nlast'); "
                "sys.stderr.write('separate\\n')"
            ),
            max_output_bytes=10,
            on_stdout_line=receive,
        )
    )
    assert lines == ["first\n", "\n", "\u2603" * 70_000 + "\n", "last"]
    assert result.stdout_truncated
    assert result.stderr == "separate\n"


def test_callback_failure_terminates_process() -> None:
    pids: list[int] = []

    async def receive(line: str) -> None:
        pids.append(int(line))
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        asyncio.run(
            run_command(
                _python("import os, time; print(os.getpid(), flush=True); time.sleep(60)"),
                on_stdout_line=receive,
            )
        )
    assert not _is_running(pids[0])


def test_callback_failure_with_full_pipe() -> None:
    async def scenario() -> None:
        async def receive(line: str) -> None:
            # Give the child enough time to fill the pipe before stopping reads.
            await asyncio.sleep(0.1)
            raise RuntimeError("callback failed")

        with pytest.raises(RuntimeError, match="callback failed"):
            await asyncio.wait_for(
                run_command(
                    _python(
                        "import sys; print('ready', flush=True); "
                        "sys.stdout.write('x' * 10000000); sys.stdout.flush()"
                    ),
                    on_stdout_line=receive,
                ),
                timeout=5,
            )

    asyncio.run(scenario())


def test_empty_argv() -> None:
    with pytest.raises(ValueError, match="argv"):
        asyncio.run(run_command([]))


def test_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        asyncio.run(run_command([str(tmp_path / "missing-executable")]))


def test_negative_output_cap() -> None:
    with pytest.raises(ValueError, match="max_output_bytes"):
        asyncio.run(run_command(_python("pass"), max_output_bytes=-1))
