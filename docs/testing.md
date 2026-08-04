# Testing

The normal suite uses local stubs and fake HTTP servers. It does not call OpenAI or Anthropic models.

## Quick syntax checks

```bash
bash -n bin/airlock \
  scripts/setup.sh scripts/install.sh scripts/doctor.sh \
  plugins/airlock/scripts/agent-guard.sh \
  plugins/airlock/scripts/secret-guard.sh \
  plugins/airlock/scripts/worktree-create.sh \
  plugins/airlock/scripts/worktree-remove.sh \
  tests/stub-claude.sh tests/test-airlock.sh tests/test-setup.sh
```

```bash
python -m py_compile \
  bin/airlock-access.py bin/airlock-router.py bin/airlock-hybrid.py \
  plugins/airlock/scripts/agent-guard.py \
  plugins/airlock/scripts/secret-guard.py \
  plugins/airlock/scripts/file_safety.py \
  plugins/airlock/scripts/worktree.py \
  scripts/update-bundle.py tests/test-setup-pty.py
```

## Python tests

```bash
python tests/test-access.py
python tests/test-agent-guard.py
python tests/test-router.py
python tests/test-hybrid.py
python tests/test-secret-guard.py
python tests/test-worktree.py
python tests/test-platform.py
python tests/test-release.py
python tests/test-docs.py
```

The native tests cover:

- exact Agent models, effort inheritance, and pinned efforts
- built-in Explore, Plan, and general-purpose inheritance
- allowed exact per-call model IDs for built-ins
- profile, Fast, and extra-usage model guards
- plain OpenAI routing without the hybrid router
- both hybrid root directions through one loopback router
- exact body forwarding and SSE streaming
- Claude authorization forwarding only to Anthropic
- Claude authorization and capability stripping before OpenAI
- unknown-model and redirect rejection
- parent-process router shutdown
- native Agent permissions with no legacy transport Bash permission
- absence of every removed legacy transport file
- one-level root fan-out and named-Agent recursion blocking
- native WorktreeCreate and WorktreeRemove hooks
- dirty tracked and eligible untracked snapshot projection
- key-only tracked env copies
- ignored, credential, private-key, link, reparse, size, and unstable-file blocking
- changed-worktree preservation
- managed bundle hashes and stale-install failure
- line endings, release metadata, documentation links, and writing rules

## Bash launcher tests

```bash
bash tests/test-airlock.sh
bash tests/test-setup.sh
python tests/test-setup-pty.py
```

These tests replace Claude Code and provider commands with stubs. They verify old-config compatibility, saved OpenAI and hybrid roots, explicit overrides, exact Agent catalogs, allowed tools, model allowlists, full setup labels, worker effort inheritance and pins, invalid input, and backups without using OAuth or model quota.

`test-setup-pty.py` runs the guided flow through a real POSIX terminal six times: a plain 80 column run, a color-capable run, a redirected-output run, a 40 column run, a color keyboard run, and a no-color keyboard run. It checks the ASCII wordmark and introduction, six numbered sections and progress track, full Claude and GPT names and IDs, recommended markers and Enter hints, honest effort wording, Claude Fable 5, separate Claude Code and Codex OAuth wording, the grouped review screen, hidden Advanced details, and saved hybrid defaults. The keyboard runs send real Up and Down escape sequences, prove wraparound and Enter selection, and keep number, name, and `?` input working. The suite also checks that plain or redirected streams contain no escape sequences, long macOS-style config paths use a stacked layout, and wrapped lines fit the terminal. Every run reports its start and finish. A timeout terminates the full child process group within a fixed grace period and reports the last output plus unsent key count. The suite prints a skip on Windows, where the Python PTY module is unavailable.

## macOS and Linux installer test

```bash
bash tests/test-install.sh
```

This test installs into a temporary folder with fake commands. It checks the installed file set and modes, managed-file conflicts, missing Claude Code, automatic proxy installation, signed-out Codex OAuth with and without approved login, writable proxy config and state fallback selection, private directory modes, generated Homebrew service environment, the `airlock proxy auth` wrapper, separate Claude login reporting, and doctor failure for an unhealthy proxy. It exits without running on other systems, where `tests/test-windows.ps1` covers the same ground.

## Windows tests

From PowerShell:

```powershell
powershell -NoProfile -File .\tests\test-windows.ps1
```

The test installs into a temporary folder with fake commands. It checks PowerShell parsing, missing Claude Code and proxy refusal, managed-file conflicts, separate Claude login reporting, router and worktree-hook installation, saved Claude and GPT hybrid roots, explicit OpenAI override behavior, old-config compatibility, invalid profile refusal, and doctor failure for an unhealthy proxy.

## JSON files

```bash
for file in config/*-agents.json; do
  python -m json.tool "$file" > /dev/null
done
python -m json.tool config/managed-bundle.json > /dev/null
python -m json.tool plugins/airlock/.claude-plugin/plugin.json > /dev/null
python -m json.tool plugins/airlock/hooks/hooks.json > /dev/null
```

## Managed bundle hashes

Check that the marker matches every managed component:

```bash
python scripts/update-bundle.py --check
```

After an intentional launcher, helper, Agent catalog, or plugin change, refresh and rerun the suite:

```bash
python scripts/update-bundle.py
```

Review the marker change before committing it.

## Router protocol tests

`tests/test-router.py` uses local fake Anthropic and OpenAI servers with synthetic authorization values.

It verifies:

- exact request-body preservation
- provider-specific header handling
- token-count routing
- unknown, malformed, and oversized request rejection
- redirect rejection with a sanitized upstream error
- upstream status and error-body pass-through
- streamed bytes arriving before upstream completion
- stream timeout separation from the shorter connection timeout
- upstream header and stream timeout handling
- partial streams and client disconnect recovery
- concurrent request handling
- bounded diagnostics without prompts, bodies, headers, or credentials
- token-count observation for streamed and non-streamed responses, including an upstream that sends no usage at all
- local health endpoints
- production upstream and startup identity restrictions
- router exit after owner exit

No real login or provider request is used.

## Worktree safety tests

`tests/test-worktree.py` creates a temporary Git repository. It proves that a native snapshot:

- contains staged, unstaged, and eligible untracked changes
- contains only env key projections
- excludes ignored and credential-bearing files
- leaves the main branch, index, and checkout unchanged
- starts clean in the isolated worktree
- preserves changed or committed worktrees
- rejects unsafe names and escaping links

Only fake credential strings are used.

## Clean worktree check

This catches line-ending and missing-file problems that a busy working tree can hide:

```bash
temp_dir="$(mktemp -d)"
git worktree add --detach "$temp_dir/worktree" HEAD
python "$temp_dir/worktree/tests/test-platform.py"
bash "$temp_dir/worktree/tests/test-airlock.sh"
git worktree remove --force "$temp_dir/worktree"
rmdir "$temp_dir"
```

Run this only when `HEAD` contains the code you want to test. It does not include uncommitted changes.

## Patch checks

```bash
git diff --check
git status --short
```

Do not use `git reset`, `git clean`, or automatic staging to fix a test. Preserve unrelated work.

## Live tests

Live tests use subscription quota and can send repository content to a provider. They are never part of CI, setup, or the doctor.

Read [Live tests](live-tests.md) and get exact permission before running one. The permission must name provider, exact model, repository files, public web state, extra-usage state, Fast state, and worker count.
