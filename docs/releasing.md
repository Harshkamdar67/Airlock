# Release process

This project does not publish from a developer laptop. A version tag starts the GitHub release workflow after the work has been reviewed and pushed.

## Current release target

```text
0.1.0-beta.10
```

The current working tree is not a release until its changes are reviewed, committed, and pushed by the maintainer.

## Before the weekend release

Check these items before changing the remote or pushing:

- Confirm the final GitHub owner and repository name.
- Rename the remote repository to the approved Airlock slug.
- Replace old clone, issue, security, and contributor URLs where needed.
- Check the Airlock name again on GitHub, npm, PyPI, common package managers, domains, and trademark databases.
- Confirm that the original Claudex MIT license and Miguel Torrez credit remain.
- Confirm that `raine/claude-code-proxy` credit remains clear.
- Review every untracked file that will become part of the release.
- Confirm no credential, account ID, email address, token, prompt, or private repository content is present.

Do not change the current remote as part of ordinary implementation work.

## Version files

The version appears in:

```text
VERSION
plugins/airlock/.claude-plugin/plugin.json
bin/airlock-access.py
CHANGELOG.md
README.md
```

Run:

```bash
python tests/test-release.py
```

The test fails when the main version and plugin version do not match.

## Test the release commit

Run the complete local suite from [Testing](testing.md).

Required checks include:

```bash
python tests/test-router.py
python tests/test-hybrid.py
python tests/test-agent-guard.py
python tests/test-secret-guard.py
python tests/test-worktree.py
python tests/test-platform.py
python tests/test-release.py
python tests/test-update.py
python tests/test-update-notice.py
python tests/test-docs.py
bash tests/test-airlock.sh
bash tests/test-setup.sh
python tests/test-setup-pty.py
git diff --check
git status --short
```

Run the Windows stub tests on Windows:

```powershell
powershell -NoProfile -File .\tests\test-windows.ps1
```

Create a clean worktree from the release commit and run the launcher tests there. This confirms that no result depends on a local untracked helper or Windows line-ending conversion.

## Live checks

Read [Live tests](live-tests.md).

Live tests need separate permission because they use subscription quota and may send approved repository files to a provider. Record the exact provider, model, files, and worker limit before starting.

At minimum, complete the required native parity matrix in the live-test guide. It covers:

- plain OpenAI root and inherited Explore
- both hybrid root directions
- exact OpenAI and Anthropic Agents in one session
- schema-valid cross-provider Explore family aliases with verified exact targets
- native model cards, tools, background overlap, cancellation, and usage
- filtered native worktree isolation
- a response longer than 120 seconds
- router credential separation and no duplicate request
- Luna implementation boundaries and stronger-model integration

Do not run live tests from the release workflow.

## Update the changelog

Move finished items from `Unreleased` into the release version and date. Keep entries short and written for users.

Use these headings when they apply:

```text
Added
Changed
Fixed
Security
Removed
```

## Commit and review

The maintainer chooses the branch, commits, and pull request. This project does not auto-stage files.

Before merge:

- review the full diff
- verify new files are intentional
- confirm generated archives are not committed
- confirm CI passes on Linux, macOS, and Windows
- confirm no live quota test ran without permission

## Protect public contributions

Before announcing the repository, configure these GitHub settings. Some controls may appear only after the repository becomes public.

- Enable private vulnerability reporting.
- Enable the dependency graph, dependency vulnerability alerts, Dependabot security updates, secret scanning, and push protection.
- Keep the default workflow token permission read-only and do not allow workflows to approve pull requests.
- Allow squash merges, disable merge commits and rebase merges, and delete merged branches automatically.
- Add a `main` branch ruleset that requires a pull request, one CODEOWNER approval, dismissal of stale approvals, approval after the latest push, resolved review conversations, and the Ubuntu, macOS, and Windows jobs from the `test` workflow.
- Require branches to be up to date before merge, block force pushes and deletion, and do not allow bypass except for an emergency repository owner path.
- Add a tag ruleset for `v*` that limits tag creation and deletion to maintainers. Do not allow release tags to be moved or recreated.
- Create a protected GitHub environment named `release`. Require maintainer approval, allow deployments only from `v*` tags, and store no credentials in it. The release job targets this environment, so a pushed tag still cannot publish without approval.
- Keep third-party Actions pinned to full commit hashes. Dependabot may open reviewed updates for those pins.

The repository includes CODEOWNERS, pull request and issue templates, a restricted workflow token declaration, and Dependabot configuration. These files guide contributions, but the GitHub ruleset is what enforces review and passing checks.

Recheck the rules from a non-owner test account before accepting the first outside contribution.

## Tag and publish

After the release commit is merged into protected `main` and every required check is green:

```bash
git switch main
git pull --ff-only origin main
git tag -a v0.1.0-beta.6 -m "Airlock v0.1.0-beta.6"
git push origin v0.1.0-beta.6
```

Pushing the tag is an outward action. Confirm it immediately before running the command.

The tag starts `.github/workflows/release.yml`. The workflow:

1. Waits for approval through the protected `release` environment.
2. Refuses a tag whose commit is not contained in `origin/main`.
3. Checks that the tag matches `VERSION`.
4. Runs the non-model test suite and managed-bundle check.
5. Builds source ZIP and tar archives from tracked Git files.
6. Creates SHA256 checksums.
7. Adds GitHub build provenance.
8. Creates a prerelease when the version contains a prerelease suffix.

The workflow does not publish to npm, PyPI, Homebrew, or Scoop. Users run `airlock update --check` for a manual notice and `airlock update` for a confirmed, verified installation from these release assets. Normal startup never checks GitHub. The manual and rollback paths are in [Updating Airlock](updating.md).

## After release

- Download both archives and verify `SHA256SUMS`.
- Install from an archive on one clean macOS or Linux machine.
- Install from an archive on one clean Windows machine.
- Run the matching doctor script.
- Run `airlock update --check` from the previous release, confirm it reports the new release, then start a fresh Airlock session and confirm the cached notice is user-visible without appearing in model context.
- Confirm startup made no GitHub request and malformed or stale notice state is silent.
- Check README links from the public repository page.
- Open a test issue only if the issue template and security instructions are correct.
- Announce only after the release page and install paths work.

## Rollback

If a release is unsafe, mark it as withdrawn and explain the affected versions. Do not silently replace archives under the same tag.

Fix the problem, increase the version, run the full checks again, and publish a new tag.
