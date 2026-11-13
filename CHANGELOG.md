# Changelog

## 0.1.0

### Added

- Local-first CLI for starting, inspecting and resuming ticket workflow runs,
  with TOML configuration and a persistent local run store.
- GitHub ticket validation, Question solution selection, child creation and
  native parent and dependency relationships.
- Provider adapters for the unmodified Claude and Codex CLIs using
  `subscription_login`, with structured handoffs and capability declarations.
- Worktree preparation, implementation, gate verification, pull request delivery
  and CI monitoring.
- Review decisions, bounded repair attempts and developer merge approval bound
  to the exact head commit SHA.
- Recorded operations for recovery, artifacts with SHA-256 checksums, usage
  records and stage timing reports.
- User guides for configuration, workflow, prerequisites and failure handling.

### Changed

- Package metadata, the Python version constant and CLI version output now
  identify the release as `0.1.0`.

### Known limitations

- Requires local Git, authenticated GitHub CLI access, a configured GitHub
  Project and provider CLIs with the user's own sign-in. Fugue does not
  provision credentials, subscriptions or Project fields.
- Both bundled provider adapters reject `api_key` authentication mode. Provider
  restrictions, quota limits and missing credentials require intervention.
- Default gate commands assume Python tooling; other repositories must
  configure their own gate. Passing the gate does not establish green CI.
- Run state and artifacts remain local. Provider capabilities and usage
  reporting vary; cost estimates are not a bill.
- Solution selection and merge approval require developer decisions by default.
  New head commits invalidate approval.
- In single-account GitHub use, reviews use COMMENT because the author cannot
  approve their own pull request. A clean required review and Fugue approval
  still have distinct purposes.
- Stacks (stacked pull requests) are deferred from 0.1.0. Work in
  [issue #49](https://github.com/Folt-CLI/fugue/issues/49) moved to the
  [0.2.0 Epic #76](https://github.com/Folt-CLI/fugue/issues/76).
- A reviewer identity setup command is deferred to
  [issue #77](https://github.com/Folt-CLI/fugue/issues/77).
- Branch protection enforcing approvals is deferred to
  [issue #78](https://github.com/Folt-CLI/fugue/issues/78).
- Follow-up hardening work remains deferred and is collected in
  [issue #53](https://github.com/Folt-CLI/fugue/issues/53).

See [limitations and failure handling](docs/user/limitations.md) for details.
