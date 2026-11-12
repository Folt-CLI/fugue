import asyncio
import shlex
import sys
from pathlib import Path

import pytest

from fugue.gate import format_failure, run_gate


def _python(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def test_all_pass(tmp_path: Path) -> None:
    commands = [
        _python("from pathlib import Path; print(Path.cwd())"),
        _python("import sys; print('warning', file=sys.stderr)"),
        _python("pass"),
    ]
    logs = tmp_path / "nested" / "logs"
    result = asyncio.run(run_gate(commands, tmp_path, log_dir=logs))
    assert result.passed
    assert result.failed_step is None
    assert len(result.steps) == 3
    for index, step in enumerate(result.steps, start=1):
        assert step.argv == commands[index - 1]
        assert step.passed
        assert step.returncode == 0
        assert not step.timed_out
        assert step.excerpt == ""
        assert step.duration_seconds >= 0
        assert step.log_path is not None
        assert step.log_path == logs / f"{index}-{Path(sys.executable).name}.log"
        assert step.log_path.is_file()
    assert result.steps[0].log_path is not None
    assert result.steps[0].log_path.read_text() == f"{tmp_path}\n"
    assert result.steps[1].log_path is not None
    assert result.steps[1].log_path.read_text() == "warning\n"
    assert result.steps[2].log_path is not None
    assert result.steps[2].log_path.read_bytes() == b""
    assert len(list(logs.iterdir())) == 3
    assert format_failure(result) == "Gate passed."


def test_empty_gate(tmp_path: Path) -> None:
    result = asyncio.run(run_gate([], tmp_path, log_dir=tmp_path / "logs"))
    assert result.passed
    assert result.steps == ()
    assert result.failed_step is None


def test_fail_fast(tmp_path: Path) -> None:
    commands = [
        _python("pass"),
        _python("print('failure'); raise SystemExit(7)"),
        _python("from pathlib import Path; Path('unexpected').touch()"),
    ]
    result = asyncio.run(run_gate(commands, tmp_path, log_dir=tmp_path / "logs"))
    assert not result.passed
    assert len(result.steps) == 2
    assert result.failed_step is not None
    assert result.failed_step is result.steps[-1]
    assert result.failed_step.returncode == 7
    assert result.failed_step.excerpt == "failure\n"
    assert not (tmp_path / "unexpected").exists()


@pytest.mark.parametrize(
    ("lines", "byte_cap", "expected"),
    [
        (2, 100, "second\nlast\n"),
        (10, 8, "nd\nlast\n"),
        (2, 8, "nd\nlast\n"),
        (0, 100, ""),
        (10, 0, ""),
    ],
)
def test_excerpt_caps(tmp_path: Path, lines: int, byte_cap: int, expected: str) -> None:
    result = asyncio.run(
        run_gate(
            [_python("print('first\\nsecond\\nlast'); raise SystemExit(1)")],
            tmp_path,
            log_dir=tmp_path / "logs",
            excerpt_lines=lines,
            max_excerpt_bytes=byte_cap,
        )
    )
    assert result.failed_step is not None
    excerpt = result.failed_step.excerpt
    assert excerpt == expected
    assert len(excerpt.encode()) <= byte_cap
    assert len(excerpt.splitlines()) <= lines


@pytest.mark.parametrize("byte_cap", [1, 2, 3, 4, 7])
def test_excerpt_utf8_boundary(tmp_path: Path, byte_cap: int) -> None:
    result = asyncio.run(
        run_gate(
            [_python("import sys; sys.stdout.write('雪' * 10); sys.exit(1)")],
            tmp_path,
            log_dir=tmp_path / "logs",
            max_excerpt_bytes=byte_cap,
        )
    )
    assert result.failed_step is not None
    assert len(result.failed_step.excerpt.encode()) <= byte_cap


def test_full_combined_log_and_tail(tmp_path: Path) -> None:
    result = asyncio.run(
        run_gate(
            [
                _python(
                    "import os; os.write(1, b'x' * 100000 + b'\\n'); "
                    "os.write(2, b'y' * 100000 + b'\\n'); "
                    "os.write(1, b'last\\n'); raise SystemExit(1)"
                )
            ],
            tmp_path,
            log_dir=tmp_path / "logs",
        )
    )
    step = result.failed_step
    assert step is not None
    assert step.log_path is not None
    output = step.log_path.read_bytes()
    assert len(output) == 200007
    assert output.count(b"x") == output.count(b"y") == 100000
    assert b"last\n" in output
    assert step.excerpt == output[-8192:].decode()


def test_timeout(tmp_path: Path) -> None:
    result = asyncio.run(
        run_gate(
            [_python("import time; print('ready', flush=True); time.sleep(60)")],
            tmp_path,
            log_dir=tmp_path / "logs",
            step_timeout=0.5,
        )
    )
    assert not result.passed
    assert result.failed_step is not None
    assert result.failed_step.timed_out
    assert not result.failed_step.passed
    assert result.failed_step.returncode != 0
    assert result.failed_step.excerpt == "ready\n"
    assert "timed out" in format_failure(result)


def test_missing_executable(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing-executable")
    result = asyncio.run(run_gate([[missing]], tmp_path, log_dir=tmp_path / "logs"))
    assert not result.passed
    step = result.failed_step
    assert step is not None
    assert step.returncode is None
    assert not step.timed_out
    assert not step.passed
    assert step.duration_seconds >= 0
    assert "Command could not start" in step.excerpt
    assert missing in step.excerpt
    assert step.log_path is not None
    assert step.log_path.read_text() == step.excerpt


def test_format_failure(tmp_path: Path) -> None:
    argv = _python("print('failure details'); raise SystemExit(9)")
    result = asyncio.run(run_gate([argv], tmp_path, log_dir=tmp_path / "logs"))
    assert result.failed_step is not None
    report = format_failure(result)
    assert shlex.join(argv) in report
    assert "Return code: 9" in report
    assert "failure details\n" in report
    assert str(result.failed_step.log_path) in report


@pytest.mark.parametrize(
    ("lines", "byte_cap", "field"),
    [(-1, 8192, "excerpt_lines"), (80, -1, "max_excerpt_bytes")],
)
def test_negative_excerpt_limits(tmp_path: Path, lines: int, byte_cap: int, field: str) -> None:
    with pytest.raises(ValueError, match=field):
        asyncio.run(
            run_gate(
                [],
                tmp_path,
                log_dir=tmp_path / "logs",
                excerpt_lines=lines,
                max_excerpt_bytes=byte_cap,
            )
        )


def test_empty_command(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="argv"):
        asyncio.run(run_gate([[]], tmp_path, log_dir=tmp_path / "logs"))
