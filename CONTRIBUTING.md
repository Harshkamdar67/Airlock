# Contributing

Thanks for helping with Airlock.

This project connects several fast-moving tools. Small, tested changes are easier to review than large rewrites.

## Before you start

- Search existing issues and pull requests.
- Read [README.md](README.md), [SECURITY.md](SECURITY.md), and [the threat model](docs/threat-model.md).
- Open an issue before a large behavior or compatibility change.
- Use a private GitHub Security Advisory for a security problem.

Do not put tokens, account details, private prompts, `.env` values, or private repository content in an issue.

## Set up a development checkout

Install Git, Python 3, Bash, and Claude Code. Provider login is not required for the normal test suite.

Run:

```bash
python tests/test-platform.py
bash tests/test-airlock.sh
bash tests/test-setup.sh
python tests/test-setup-pty.py
```

The tests use stubs and do not make model requests.

See [Testing](docs/testing.md) for the complete suite.

## Make a change

- Preserve unrelated working-tree changes.
- Do not stage, reset, clean, commit, or push files that are not part of your change.
- Keep the proxy on `127.0.0.1`.
- Do not read or copy provider credential files.
- Do not change native Claude, native Codex, global hooks, plugins, MCP settings, or unrelated agents.
- Keep exact worker, effort, and provider checks fail closed.
- Add a regression test for every bug fix.
- Keep macOS, Linux, Windows, and WSL2 behavior in mind.

## Writing style

Write for someone who has not read the code.

- Use short sentences.
- Put installation and the first useful command near the top.
- Explain a technical word the first time it appears.
- Prefer examples over long descriptions.
- Do not use em dashes.
- Do not claim a provider guarantee that is not in official documentation.
- Move detailed reference material into `docs/` instead of growing the README.

Run:

```bash
python tests/test-docs.py
```

## Tests and live quota

Normal tests must stay free of model calls.

A live test needs clear permission that names the provider, model, repository files, public web access, extra usage, and worker count. Do not add live tests to CI.

See [Live tests](docs/live-tests.md).

## Pull requests

A pull request should include:

- the problem in one or two sentences
- the chosen fix
- files and behavior changed
- tests run and their results
- known limits or work left
- whether any live model request ran

Keep generated archives and local state out of the commit.

## Sign your commits

This project uses the [Developer Certificate of Origin](https://developercertificate.org/). It is a short statement that you wrote the change, or that you have the right to submit it under the project's license. There is no separate agreement to sign.

Add the sign-off line with `-s`:

```bash
git commit -s -m "Fix the router timeout"
```

Git appends one line to the message:

```text
Signed-off-by: Your Name <you@example.com>
```

The name and email must be real and must match your commit author. To sign off on work you already committed:

```bash
git commit --amend -s --no-edit
```

## Provider and upstream changes

Protocol translation, Codex OAuth, and many compatibility details belong to [`raine/claude-code-proxy`](https://github.com/raine/claude-code-proxy).

If a bug reproduces with the proxy directly, report it upstream. If it happens only through `airlock`, report it here.

## License

By contributing, you agree that your contribution is released under the repository's MIT License.
