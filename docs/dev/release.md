# Release instructions

Release preparation happens on an issue branch from `develop`, in its dedicated
worktree. Release promotion uses a pull request from `develop` to `main`.
Follow the [development process](process.md) and [workflow](workflow.md).

## Prepare the release

1. Set the same release version in `pyproject.toml` and
   `src/fugue/__init__.py`. For this release both must be `0.1.0`.
2. Add a version entry to `CHANGELOG.md` with Added, Changed and Known limitations
   sections. List delivered features only and use the [glossary](../../GLOSSARY.md)
   terms. Do not describe planned work as available.
3. Update `tests/test_version.py` to verify the release version, agreement with
   project metadata and CLI version output. Compare the Python version constant
   with installed distribution metadata when available; otherwise compare it
   with `pyproject.toml`.
4. Run `uv lock --offline` to update the project's own version in `uv.lock`.
   Confirm that only the `fugue` version changed in the lockfile. Do not add
   dependencies as part of release preparation.

## Verify and deliver

The orchestrator runs the [gate](testing.md) in the release worktree:

```sh
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv build
```

Record actual verification results and material risks in the pull request.
Build outputs stay out of Git. Complete the required read-only review and CI
before requesting developer merge approval. Approval applies only to the exact
head commit SHA; any new commit requires fresh approval.

Merge the preparation pull request into `develop` through the normal developer
merge process, then promote `develop` to `main` through a release pull request.
Do not treat this preparation as publication of a package or a hosted release.
Close the release Epic with Disposition Released only after all children have
closed and the release has been delivered, following the
[Epic closure rules](issue-format.md#epics).
