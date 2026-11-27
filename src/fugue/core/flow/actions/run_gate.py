"""Run the checks of a gate."""

import shlex
from collections.abc import Mapping
from pathlib import Path

from fugue.core.flow.actions.action import Action
from fugue.core.process import run_command
from fugue.core.reporting import Output, PartFinished, PartStarted
from fugue.errors import FlowError
from fugue.models import CheckResult, GateResult

KEEP = 20000
"""How much of a check's output is kept in its result, from the end, where failures are."""


class RunGate(Action):
    async def perform(
        self, step: str, checks: Mapping[str, str], cwd: Path, *, timeout: float | None = None
    ) -> GateResult:
        """Run each check in `cwd`, in order, and every one even after one fails, so the result
        says everything that is wrong at once.

        A check is a name and a command (split like a shell would). Its output is reported as it
        is printed (`Output` on stdout and stderr), and the check as a part of `step`.
        """
        results = []
        for name, command in checks.items():
            await self.reporter.report(PartStarted(step=step, part=name))
            result = await self._run(name, command, cwd, timeout)
            await self.reporter.report(
                PartFinished(step=step, part=name, ok=result.passed, detail=result.detail)
            )
            results.append(result)
        return GateResult(checks=tuple(results))

    async def _run(self, name: str, command: str, cwd: Path, timeout: float | None) -> CheckResult:
        try:
            argv = shlex.split(command)
        except ValueError as error:
            raise FlowError(
                f"the check {name!r} has a command that cannot be read: {error}"
            ) from error
        if not argv:
            raise FlowError(f"the check {name!r} has no command")
        await self.reporter.report(Output(stream="stdout", text=f"$ {command}"))

        async def out(line: str) -> None:
            await self.reporter.report(Output(stream="stdout", text=line.rstrip("\n")))

        async def err(line: str) -> None:
            await self.reporter.report(Output(stream="stderr", text=line.rstrip("\n")))

        try:
            done = await run_command(
                argv, cwd=cwd, timeout=timeout, on_stdout_line=out, on_stderr_line=err
            )
        except FileNotFoundError:
            await err(f"{argv[0]}: command not found")
            return CheckResult(name=name, command=command, passed=False, stderr="command not found")
        return CheckResult(
            name=name,
            command=command,
            passed=done.returncode == 0 and not done.timed_out,
            exit_code=done.returncode,
            timed_out=done.timed_out,
            seconds=done.duration_seconds,
            stdout=done.stdout[-KEEP:],
            stderr=done.stderr[-KEEP:],
        )
