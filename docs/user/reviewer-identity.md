# Reviewer identity setup

GitHub prevents the author of a pull request from submitting an APPROVE or
REQUEST_CHANGES review on that pull request. A reviewer identity is the GitHub identity used to submit reviews; a separate
reviewer identity differs from the pull request author. A reviewer app is a private GitHub App
registered for that purpose and owned by you or your organization.

Fugue can register a reviewer app and save its private key. **Fugue does not yet
post reviews as the reviewer app.** Registration alone does not change the
identity used by workflow commands.

## Register and install

Run this on your local machine, with a browser signed into GitHub:

```sh
fugue reviewer-app setup
```

The repository comes from `github.repo` in `fugue.toml`, or the GitHub `origin`
remote. You can select configuration and the repository root with `--config`
and `--repo-root`. Use `--org ORG` to register the app under an organization
instead of your personal account. You need permission to register an app there.

1. Fugue starts a temporary HTTP server on `127.0.0.1` with an ephemeral port,
   prints a local start URL and tries to open it in your browser. If the browser
   cannot open, open that URL manually on the same machine.
2. The start page posts a manifest to GitHub. Check the app name and permissions
   on GitHub and create the app. The app is private, subscribes to no events and
   has no webhook configured.
3. GitHub redirects your browser to the local callback with a temporary code and
   callback state. Fugue checks the state, accepts one callback and closes the
   server. Invalid states are rejected while it keeps waiting.
4. Fugue exchanges the code with GitHub over HTTPS and saves the returned private
   key locally, then saves a record containing only the app id, slug, repository
   and key path. Other conversion credentials are discarded.
5. Fugue prints the permissions, installation URL, key path and configuration
   line. Follow the installation URL and select the intended repository. Fugue
   does not verify installation or record an installation id.

The callback timeout defaults to 300 seconds; change it with `--timeout SECONDS`.
Closing the browser without a callback waits until the timeout. Cancellation,
GitHub callback errors and timeouts exit with code 4; interrupting the command
exits with code 6. Neither writes files before conversion. Each attempt uses a
new callback state.

## Permissions

- `pull_requests: write`: post reviews, reply to review threads and resolve them.
- `contents: read`: read the diff.
- `metadata: read`: implicit for GitHub Apps.

These are the permissions declared in the manifest. No additional permissions
or event subscriptions are requested.

## Local files and recovery

Files live in `$XDG_CONFIG_HOME/fugue/reviewer-apps/OWNER__NAME/`, or
`~/.config/fugue/reviewer-apps/OWNER__NAME/` when `XDG_CONFIG_HOME` is unset.
They do not use `XDG_STATE_HOME` or Fugue's run store.

The repository's app directory has mode `0700`; `private-key.pem` and `app.json`
have mode `0600`. The private key, once received from GitHub, never leaves the
machine. Fugue never prints the key or the conversion response. Keep the key
private and retain it with the app record.

An existing `app.json` stops setup with exit code 2 and its path. To deliberately
start over, remove that record yourself and deal with any existing app and key
first: setup never overwrites either file. There is no removal, replacement or
rotation command. If conversion or saving fails, the app may already exist:
delete it at <https://github.com/settings/apps> (or your organization's app
settings) before retrying. A key can remain if saving the record failed.

This flow supports local machines and github.com. Remote or headless machines,
GitHub Enterprise hosts and Windows permission handling are outside its scope.
The manifest flow is described in the
[GitHub documentation](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest).

## Review configuration

In the default single-account mode, `reviewer_identity_separate = false`, Fugue
submits COMMENT reviews, even when there are no blocking findings. This avoids
GitHub's restriction on author reviews. Fugue's recorded merge approval remains
a separate decision.

Only after your workflow actually uses a separate reviewer identity, enable:

```toml
[github]
reviewer_identity_separate = true
```

This setting selects APPROVE for a clean review or REQUEST_CHANGES for blocking
findings. It does not load the saved key, switch credentials or enable posting
as the reviewer app. The setup command prints this line but does not edit
`fugue.toml`.
