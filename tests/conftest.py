"""Make every test independent of the terminal environment of the host."""

import pytest


@pytest.fixture(autouse=True)
def plain_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FORCE_COLOR", "TTY_COMPATIBLE", "TTY_INTERACTIVE", "LINES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("COLUMNS", "200")
