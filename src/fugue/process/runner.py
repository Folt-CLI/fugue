"""Execute commands with bounded capture and process group cleanup."""

import asyncio
import os
import signal
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int | None
    timed_out: bool
    cancelled: bool
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    callback_line_truncated: bool
    full_output_path: Path | None
    duration_seconds: float


class _Capture:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.total = 0
        self.first = bytearray()
        self.last = bytearray()
        self.callback_line_truncated = False

    @property
    def truncated(self) -> bool:
        return self.total > self.limit

    def add(self, data: bytes) -> None:
        self.total += len(data)
        first_limit = self.limit // 2
        first_size = min(len(data), first_limit - len(self.first))
        self.first.extend(data[:first_size])
        self.last.extend(data[first_size:])
        last_limit = self.limit - first_limit
        if len(self.last) > last_limit:
            del self.last[: len(self.last) - last_limit]

    def text(self) -> str:
        if not self.truncated:
            return (self.first + self.last).decode("utf-8", errors="replace")
        omitted = self.total - self.limit
        marker = f"\n[... truncated {omitted} bytes ...]\n"
        return (
            self.first.decode("utf-8", errors="replace")
            + marker
            + self.last.decode("utf-8", errors="replace")
        )


class _Spool:
    """Spool both streams in the order their chunks are received."""

    def __init__(self, directory: Path | None) -> None:
        self.directory = directory
        self.pending = bytearray()
        self.file: BinaryIO | None = None
        self.path: Path | None = None
        self._files = ExitStack()

    def add(self, data: bytes, truncated: bool) -> None:
        if self.directory is None:
            return
        if self.file is None:
            self.pending.extend(data)
            if not truncated:
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix="fugue-", suffix=".log", dir=self.directory)
            self.file = self._files.enter_context(os.fdopen(fd, "wb"))
            self.path = Path(name)
            self.file.write(self.pending)
            self.pending.clear()
        else:
            self.file.write(data)

    def close(self) -> None:
        self._files.close()


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = asyncio.get_running_loop().time() + 2.0
    while asyncio.get_running_loop().time() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


async def _read_stream(
    stream: asyncio.StreamReader,
    capture: _Capture,
    spool: _Spool,
    on_line: Callable[[str], Awaitable[None]] | None = None,
    max_line_bytes: int | None = 65536,
) -> None:
    pending = bytearray()
    line_truncated = False

    def callback_line() -> str:
        line = pending.decode("utf-8", errors="replace")
        if line_truncated and max_line_bytes is not None:
            # A capped prefix can end inside a UTF-8 character. Keep the encoded
            # callback prefix within the cap even after replacement decoding.
            line = line.encode("utf-8")[:max_line_bytes].decode("utf-8", errors="ignore")
            line += " [line truncated]"
        return line

    while data := await stream.read(65536):
        capture.add(data)
        spool.add(data, capture.truncated)
        if on_line is not None:
            segments = data.split(b"\n")
            for index, segment in enumerate(segments):
                if max_line_bytes is None:
                    pending.extend(segment)
                else:
                    remaining = max_line_bytes - len(pending)
                    pending.extend(segment[:remaining])
                    line_truncated = line_truncated or len(segment) > remaining
                    if line_truncated:
                        capture.callback_line_truncated = True
                if index < len(segments) - 1:
                    await on_line(callback_line() + "\n")
                    pending.clear()
                    line_truncated = False
    if on_line is not None and (pending or line_truncated):
        await on_line(callback_line())


async def _write_stdin(stream: asyncio.StreamWriter, stdin: str | None) -> None:
    try:
        if stdin is not None:
            stream.write(stdin.encode("utf-8"))
            await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    except asyncio.CancelledError:
        if not stream.is_closing():
            stream.transport.abort()
        raise
    finally:
        stream.close()
        with suppress(BrokenPipeError, ConnectionResetError):
            await stream.wait_closed()


async def _discard_stream(stream: asyncio.StreamReader) -> None:
    while await stream.read(65536):
        pass


async def run_command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin: str | None = None,
    timeout: float | None = None,
    max_output_bytes: int = 65536,
    max_line_bytes: int | None = 65536,
    spool_dir: Path | None = None,
    on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
) -> CommandResult:
    """Execute argv; callback lines retain newlines, including an unterminated tail.

    Callback line content is capped at max_line_bytes (65536 by default), with a
    truncation marker before any retained newline. The result field
    callback_line_truncated is True if any callback line was truncated. Setting
    max_line_bytes=None delivers complete lines and accepts unbounded line memory.

    Output capture limits apply independently to stdout and stderr. The optional
    spool contains their complete bytes in chunk arrival order. Cancellation cleans
    up and propagates, so returned results always have cancelled=False.
    """
    args = tuple(argv)
    if not args:
        raise ValueError("argv must not be empty")
    if max_output_bytes < 0:
        raise ValueError("max_output_bytes must not be negative")
    if max_line_bytes is not None and max_line_bytes < 0:
        raise ValueError("max_line_bytes must not be negative")
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=None if env is None else {**os.environ, **env},
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    stdout_stream = process.stdout
    stderr_stream = process.stderr
    stdin_stream = process.stdin
    assert stdout_stream is not None
    assert stderr_stream is not None
    assert stdin_stream is not None
    stdout = _Capture(max_output_bytes)
    stderr = _Capture(max_output_bytes)
    spool = _Spool(spool_dir)
    tasks = [
        asyncio.create_task(
            _read_stream(stdout_stream, stdout, spool, on_stdout_line, max_line_bytes)
        ),
        asyncio.create_task(_read_stream(stderr_stream, stderr, spool)),
        asyncio.create_task(_write_stdin(stdin_stream, stdin)),
    ]
    completion = asyncio.gather(*tasks, process.wait())
    cleanup_task: asyncio.Task[None] | None = None

    async def cleanup(*, discard_output: bool) -> None:
        # Signal and stop the group before awaiting any blocked I/O. Reaping is
        # deferred until readers resume, since paused pipes can delay wait().
        await _terminate_process_group(process)
        if not stdin_stream.is_closing() or stdin_stream.transport.get_write_buffer_size():
            stdin_stream.transport.abort()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if discard_output:
            readers = [
                _discard_stream(stdout_stream),
                _discard_stream(stderr_stream),
            ]
        else:
            # Resume capture without invoking callbacks that may be blocked.
            readers = [
                _read_stream(stdout_stream, stdout, spool),
                _read_stream(stderr_stream, stderr, spool),
            ]
        await asyncio.gather(*readers, completion, process.wait(), return_exceptions=True)

    timed_out = False
    deadline = asyncio.timeout(timeout)
    try:
        try:
            async with deadline:
                await asyncio.shield(completion)
        except TimeoutError:
            if not deadline.expired():
                raise
            timed_out = True
            cleanup_task = asyncio.create_task(cleanup(discard_output=False))
            await asyncio.shield(cleanup_task)
    except BaseException:
        # Shield cleanup so repeated cancellation cannot strand the process group.
        if cleanup_task is None:
            cleanup_task = asyncio.create_task(cleanup(discard_output=True))
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
        cleanup_task.result()
        raise
    finally:
        spool.close()
    return CommandResult(
        argv=args,
        returncode=process.returncode,
        timed_out=timed_out,
        cancelled=False,
        stdout=stdout.text(),
        stderr=stderr.text(),
        stdout_truncated=stdout.truncated,
        stderr_truncated=stderr.truncated,
        callback_line_truncated=stdout.callback_line_truncated,
        full_output_path=spool.path,
        duration_seconds=time.monotonic() - started,
    )
