import subprocess
import sys

from fugue import __version__


def test_version_is_nonempty_string() -> None:
    assert isinstance(__version__, str)
    assert __version__


def test_module_version_output() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fugue", "--version"], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0
    assert result.stdout == "fugue 0.1.0.dev0\n"
