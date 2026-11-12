import pytest

from fugue import __version__
from fugue.__main__ import main


def test_version_is_nonempty_string() -> None:
    assert isinstance(__version__, str)
    assert __version__


def test_version_output(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out == "fugue 0.1.0.dev0\n"


def test_no_arguments() -> None:
    assert main([]) == 2
