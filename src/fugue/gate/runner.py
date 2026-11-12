"""Run gate commands sequentially and retain complete logs."""

import re
import shlex
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from fugue.process import run_command


@dataclass(frozen=True)
class GateStepResult:
    argv: tuple[str, ...]
    passed: bool
    returncode: int | None
    timed_out: bool
    excerpt: str
    log_path: Path | None
    duration_seconds: float


@dataclass(frozen=True)
class GateResult:
    passed: bool
    steps: tuple[GateStepResult, ...]
    failed_step: GateStepResult | None


def _excerpt(path: Path, lines: int, max_bytes: int) -> str:
    if lines == 0 or max_bytes == 0:
        return ""
    with path.open("rb") as log:
        log.seek(0, 2)
        log.seek(max(0, log.tell() - max_bytes))
        tail = log.read()
    text = "".join(tail.decode("utf-8", errors="replace").splitlines(keepends=True)[-lines:])
    # Replacement characters can expand invalid or partially retained UTF-8 bytes.
    return text.encode("utf-8")[-max_bytes:].decode("utf-8", errors="ignore")


async def run_gate(
    commands: Sequence[Sequence[str]],
    cwd: Path,
    *,
    log_dir: Path,
    step_timeout: float = 1800,
    excerpt_lines: int = 80,
    max_excerpt_bytes: int = 8192,
) -> GateResult:
    """Execute commands in order, stopping at the first failure.

    Logs use one-based step indices and the sanitized executable basename.
    Excerpts retain the tail within both the line and UTF-8 byte limits.
    """
    if excerpt_lines < 0:
        raise ValueError("excerpt_lines must not be negative")
    if max_excerpt_bytes < 0:
        raise ValueError("max_excerpt_bytes must not be negative")
    steps: list[GateStepResult] = []
    log_dir.mkdir(parents=True, exist_ok=True)
    for index, command in enumerate(commands, start=1):
        argv = tuple(command)
        if not argv:
            raise ValueError("argv must not be empty")
        name = re.sub(r"[^a-zA-Z0-9_.-]", "_", Path(argv[0]).name)[:80] or "command"
        log_path = log_dir / f"{index}-{name}.log"
        started = time.monotonic()
        try:
            with TemporaryDirectory(prefix=".gate-", dir=log_dir) as spool_dir:
                result = await run_command(
                    argv,
                    cwd=cwd,
                    timeout=step_timeout,
                    max_output_bytes=0,
                    spool_dir=Path(spool_dir),
                )
                if result.full_output_path is None:
                    log_path.write_bytes(b"")
                else:
                    result.full_output_path.replace(log_path)
            passed = result.returncode == 0 and not result.timed_out
            step = GateStepResult(
                argv=argv,
                passed=passed,
                returncode=result.returncode,
                timed_out=result.timed_out,
                excerpt="" if passed else _excerpt(log_path, excerpt_lines, max_excerpt_bytes),
                log_path=log_path,
                duration_seconds=result.duration_seconds,
            )
        except FileNotFoundError as exc:
            log_path.write_text(f"Command could not start: {exc}\n", encoding="utf-8")
            step = GateStepResult(
                argv=argv,
                passed=False,
                returncode=None,
                timed_out=False,
                excerpt=_excerpt(log_path, excerpt_lines, max_excerpt_bytes),
                log_path=log_path,
                duration_seconds=time.monotonic() - started,
            )
        steps.append(step)
        if not step.passed:
            return GateResult(passed=False, steps=tuple(steps), failed_step=step)
    return GateResult(passed=True, steps=tuple(steps), failed_step=None)


def format_failure(result: GateResult) -> str:
    """Render a compact failure report for an agent."""
    step = result.failed_step
    if step is None:
        return "Gate passed."
    timeout = " (timed out)" if step.timed_out else ""
    return (
        f"Gate failed: {shlex.join(step.argv)}\n"
        f"Return code: {step.returncode}{timeout}\n"
        f"{step.excerpt}\n"
        f"Log: {step.log_path}"
    )
