"""Settings every flow has."""

from pathlib import Path

from fugue.config import Config


class FlowConfig(Config):
    """The base of every flow's settings. A flow adds its own and picks its environment prefix.

    `record_runs`: write every event of a run to `<runs_dir>/<run id>.jsonl`.
    `runs_dir`: where those files go; default `.fugue/runs` in the repository.
    """

    record_runs: bool = True
    runs_dir: Path | None = None
