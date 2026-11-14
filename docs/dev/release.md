# Release instructions

Follow the [development process](process.md) and [workflow](workflow.md).
Use the [glossary](../../GLOSSARY.md) terms in release documentation.

## Release process

1. Prepare the release on an issue branch from `develop`, in its dedicated
   worktree. Set the same version in `pyproject.toml` and
   `src/fugue/__init__.py`; for a release both must be the release version.
   Run `uv lock --offline` to update the project's own version in `uv.lock`.
   Confirm that only the `fugue` version changed in the lockfile. Do not add
   dependencies as part of release preparation. Add the release entry to
   [CHANGELOG.md](../../CHANGELOG.md) using the format below. Update
   `tests/test_version.py` to verify the release version, agreement with
   project metadata and CLI version output. Compare the Python version constant
   with installed distribution metadata when available; otherwise compare it
   with `pyproject.toml`. Complete the verification below and merge the
   preparation pull request into `develop` through the normal flow.
2. Open the release pull request from `develop` to `main`, titled
   `Epic[#<release epic>]: <version>`. Use that version's changelog entry as
   the pull request body. SQUASH merge it with the commit subject
   `Epic[#<release epic>]: <version>` and the same changelog entry as the
   commit body, following the required review and merge approval rules.
3. Create the release from `main`. Tag the squash commit `v<version>` and use
   the same changelog entry as the release notes. Package registry publication
   requires separate authorization.
4. Merge `main` back into `develop` through a pull request.
5. Close the release Epic with Disposition Released after all children have
   closed and the release has been delivered, following the
   [Epic closure rules](issue-format.md#epics).

## Changelog format

Start `CHANGELOG.md` with `# Changelog`. Each release starts with
`## <version>` and contains:

- `### Overview`: two or three sentences describing the release.
- `### Included in this release`, containing `#### Features`: one bullet per
  highlight in the form `- [#<ticket>] : <description>`.
- `#### Bugs Fixed` under Included in this release only when bugs that existed
  in a previous release are now fixed. Use the same ticket bullet format.
  Bugs introduced and fixed within the release are omitted. The first release
  has no Bugs Fixed section.

The changelog highlights delivered features and fixes; it is not exhaustive.
Never state what something lacks or does not do, and do not describe planned
work as available. Use the release entry, from its version heading through its
last section, consistently for the release pull request body, squash commit
body and release notes.

## Verify and deliver

The orchestrator runs the [gate](testing.md) in the release worktree:

```sh
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv build
```

Check installation of the built wheel in a clean environment outside the
worktree. Replace `<version>` with the release version:

```sh
release_check_dir=$(mktemp -d)
uv venv "$release_check_dir/venv"
uv pip install --python "$release_check_dir/venv/bin/python" "dist/fugue-<version>-py3-none-any.whl"
"$release_check_dir/venv/bin/fugue" --version
```

Confirm that the installed CLI reports the release version.

Record actual verification results and material risks in the pull request.
Build outputs stay out of Git. Complete the required read-only review and CI
before requesting developer merge approval. Approval applies only to the exact
head commit SHA; any new commit requires fresh approval.
