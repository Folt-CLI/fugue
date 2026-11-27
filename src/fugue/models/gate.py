"""The result of running a gate."""

from pydantic import BaseModel, ConfigDict


class CheckResult(BaseModel):
    """One check of a gate: what ran, how it ended and what it printed."""

    model_config = ConfigDict(frozen=True)

    name: str
    command: str
    passed: bool
    exit_code: int | None = None
    timed_out: bool = False
    seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""

    @property
    def detail(self) -> str:
        """A few words on how it ended, for the checklist."""
        if self.timed_out:
            return "timed out"
        if self.passed:
            return f"{self.seconds:.1f}s"
        return f"exit {self.exit_code}" if self.exit_code is not None else "did not run"


class GateResult(BaseModel):
    """Every check of a gate, in the order they ran."""

    model_config = ConfigDict(frozen=True)

    checks: tuple[CheckResult, ...] = ()

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if not check.passed)
