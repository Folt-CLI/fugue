import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

from fugue import __version__


def test_release_version() -> None:
    assert __version__ == "0.2.0"


def test_version_matches_project_metadata() -> None:
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8"))

    assert __version__ == project["project"]["version"]


def test_version_matches_distribution() -> None:
    try:
        expected = metadata.version("fugue")
    except metadata.PackageNotFoundError:
        project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
        project = tomllib.loads(project_file.read_text(encoding="utf-8"))
        expected = project["project"]["version"]

    assert __version__ == expected


def test_module_version_output() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fugue", "--version"], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0
    assert result.stdout == "fugue 0.2.0\n"
