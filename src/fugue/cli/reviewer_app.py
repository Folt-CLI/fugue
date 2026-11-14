"""Setup-only reviewer identity command."""

import asyncio
import math
import os
import re
import webbrowser
from collections.abc import Callable
from pathlib import Path

from fugue.cli import terminal, wiring
from fugue.config import FugueConfig
from fugue.reviewer_app.flow import Transport, default_transport, setup
from fugue.reviewer_app.store import app_dir, config_dir

browser_opener: Callable[[str], object] = webbrowser.open
transport_hook: Transport = default_transport


def run_setup(
    config: FugueConfig,
    root: Path,
    org: str | None = None,
    timeout: float = 300,
    open_browser: Callable[[str], object] | None = None,
    transport: Transport | None = None,
) -> int:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be positive and finite")
    if org is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", org):
        raise ValueError("Organization must be a GitHub organization name")
    repo = wiring.resolve_repo(config, root)
    directory = app_dir(config_dir(os.environ, Path.home()), repo)
    record_path = directory / "app.json"
    if os.path.lexists(record_path):
        raise ValueError(
            f"Reviewer app record already exists: {record_path}. Remove it to start over."
        )

    def ready(url: str) -> None:
        terminal.print_text(f"Open this URL to register the reviewer app: {url}")
        try:
            (open_browser or browser_opener)(url)
        except Exception:
            terminal.print_text("Browser could not open. Open the URL above manually.")

    record = asyncio.run(setup(repo, directory, org, timeout, ready, transport or transport_hook))
    terminal.print_text(
        "Permissions: pull_requests: write (post reviews, reply and resolve threads); "
        "contents: read (read the diff); metadata: read (implicit)."
    )
    terminal.print_text(f"Install: https://github.com/apps/{record.slug}/installations/new")
    terminal.print_text(f"Private key: {record.key_file}")
    terminal.print_text(
        "After configuring a separate reviewer identity, set this under [github] "
        "in fugue.toml: reviewer_identity_separate = true"
    )
    terminal.print_text("Fugue does not yet post reviews as the reviewer app.")
    return 0
