# Testing

The normal suite uses local stubs and fake HTTP servers. It does not call OpenAI or Anthropic models, and it does not contact a configured open-model server.

## Quick syntax checks

```bash
bash -n bin/airlock \
  scripts/setup.sh scripts/install.sh scripts/doctor.sh \
  plugins/airlock/scripts/agent-guard.sh \
  plugins/airlock/scripts/update-notice.sh \
  plugins/airlock/scripts/secret-guard.sh \
  plugins/airlock/scripts/worktree-create.sh \
  plugins/airlock/scripts/worktree-remove.sh \
  tests/stub-claude.sh tests/test-airlock.sh tests/test-setup.sh
```

```bash
python -m py_compile \
  bin/airlock-access.py bin/airlock-update.py bin/airlock-router.py bin/airlock-hybrid.py \
  bin/airlock_policy.py bin/airlock_openmodel.py bin/airlock_openmodel_adapter.py \
  bin/airlock_openrouter_auth.py \
  bin/airlock_openrouter_presets.py bin/airlock_openrouter_models.py \
  plugins/airlock/scripts/agent-guard.py \
  plugins/airlock/scripts/update-notice.py \
  plugins/airlock/scripts/secret-guard.py \
  plugins/airlock/scripts/file_safety.py \
  plugins/airlock/scripts/worktree.py \
  scripts/test-sol-long-context.py scripts/update-bundle.py \
  tests/test-live-context.py tests/test-setup-pty.py
```

## Python tests

```bash
python tests/test-access.py
python tests/test-agent-guard.py
python tests/test-router.py
python tests/test-hybrid.py
python tests/test-live-context.py
python tests/test-secret-guard.py
python tests/test-worktree.py
python tests/test-platform.py
python tests/test-release.py
python tests/test-update.py
python tests/test-update-notice.py
python tests/test-docs.py
python tests/test-openrouter-policy.py
python tests/test-openrouter-auth.py
python tests/test-openrouter-models.py
python tests/test-openrouter-access.py
python tests/test-openmodel-policy.py
python tests/test-openmodel.py
python tests/test-openmodel-adapter.py
python tests/test-openmodel-access.py
python tests/test-fast-session-end.py
python tests/test-web-tools.py
python tests/test-console.py
python tests/test-console-tools.py
python tests/test-console-history.py
```

`tests/test-openrouter-policy.py` covers the shared registry and session-snapshot schema: exact-field checks, the 10-route limit, separate routable-ID and canonical-identity validation, bounded provider-name, provider-slug, and quantization routing tokens, sorted and deduplicated `supported_parameters` with `tools` and `tool_choice` required, the 30-day `checked_at` freshness window, owner and file-permission checks, atomic durable writes, and compare-and-swap conflicts between concurrent writers.

`tests/test-openrouter-auth.py` covers key-shape validation and the platform credential backends with fake system calls; it never touches a real Keychain, DPAPI store, or Secret Service, and never contacts OpenRouter.

`tests/test-openrouter-models.py` covers `list`, `presets`, `add`, `add-preset`, `remove`, and `refresh` against a fake catalog fetcher. It verifies exact routable-model and endpoint-tag identity, accepts an omitted or null `alias_target` but rejects a declared non-null alias, freezes preset canonical, provider-name, provider-slug, and quantization metadata, rejects a provider-plus-quantization pair that identifies more than one endpoint, checks required tool support, prompts before catalog access, handles concurrent update conflicts, and keeps `refresh` report-only until `--apply` is passed. It also proves that listing presets is offline, no preset is enabled automatically, and preset guidance never enters registry JSON. It never sends a real HTTP request.

`tests/test-openrouter-access.py` covers how a declared registry becomes a session: hybrid-session Agent exposure, the extra-usage marker requirement, the exclusive `airlock opr` root profile, credential-free session-snapshot freezing of the endpoint tag, provider name, provider slug, quantization, and canonical response identity, and fail-closed behavior for an unregistered or invalid registry. For the `airlock opr` root profile specifically, it proves that the selected root route is always `access: "included"` and never marked extra usage regardless of the extra-usage policy, that any other declared route in that same session still follows the normal `never`/`ask`/`allow` extra-usage gating, that an unknown, disabled, or case-mismatched root route is rejected with an "unknown or disabled" error before a session can start, and that the root route is rejected for every profile other than `openrouter-pure`. A separate test proves that adding the `opr` root profile leaves hybrid-session OpenRouter exposure unchanged. It verifies that a matching preset adds only fixed, labeled community guidance to the Agent description, while preset guidance stays out of snapshots, policies, and prompts and non-preset descriptions remain neutral.

`tests/test-openmodel-policy.py` covers canonical loopback URL refusal, private printable model identities, exact registry schemas, endpoint references and disablement, current-user file protection, links and reparse points, atomic durable writes, compare-and-swap conflicts, and protocol 6 signed snapshot agreement. It uses temporary files only.

`tests/test-openmodel.py` covers endpoint and route management, required capability declarations, duplicate rejection, confirmation, safe list and error output, the explicit catalog check with a fake direct connection, and sanitized offline Doctor output. No configured inference server is contacted.

`tests/test-openmodel-adapter.py` covers text and tool round trips, representable mixed content order, immediate and complete tool-result sequencing, request allowlisting, output clamps, every declared tool mode, response-side tool counts, exact private response identities, fragmented and interleaved SSE tool calls, usage after finish, `[DONE]`, bounds, unsupported blocks, and failures before and after stream commitment. Sanitized probe fixtures contain no private local path.

`tests/test-openmodel-access.py` covers absent-registry compatibility, pure and hybrid root selectors, safe public route projection, generated Agent metadata, context windows, signed private endpoint metadata, global identity collisions, worker-disabled pure roots, and exclusion from automatic selection, failover, and compactors.

`tests/test-update.py` uses only loopback fake release servers and temporary archives. It never contacts GitHub. It covers release channels, exact checksums, optional attestations, network failures, archive traversal and links, active-session refusal, confirmation, installation, Doctor, cleanup, and update-notice cache lifecycle.

`tests/test-update-notice.py` validates the network-free SessionStart reader. It covers exact user-only output, missing and unsafe files, the size and age limits, duplicate or malformed JSON, installed-version and release-channel checks, canonical release URLs, SemVer precedence, interpreter failures, and silence on every invalid path.

`tests/test-web-tools.py` covers the bundled MCP web-tools server offline. It checks DuckDuckGo link decoding and result parsing against fixture markup, refusal of private, loopback, and non-HTTP addresses, readable-text extraction with scripts and styles removed, and the stdio protocol handshake, `tools/list`, and `tools/call`, using fake HTTP functions throughout. It never contacts the network.

`tests/test-console.py` covers the console server offline, using fake router and fixture HTTP servers. It checks registry file discovery and validation, session state and blocked-reason derivation, route status and context-fit, the HTTP API and its loopback `Origin` check, the SSE stream, the Markdown report, the router pin and unpin control endpoints, address-marker creation and safe cleanup, the process-lifetime `console.lock` that permits only one live console per runtime root, second-start refusal with a validated existing URL, hard-crash lock release, and proposal creation, editing, approval, rejection, expiry, and conflict handling. It never contacts a real provider or router.

`tests/test-console-history.py` covers the session history index over Claude Code transcripts: turn, tool, and compaction counts, peak context, subagent linking through Agent results and meta files, incremental scanning across appends, partial lines, and truncation, cache persistence with private permissions, filters, and Airlock periods from the router launch log. It reads only temporary transcripts it writes itself.

`tests/test-console-tools.py` covers the shared tool contract and the stdio MCP wrapper: exact tool names and schemas, input validation, result projection against the console-public allowlist, per-call address-marker discovery, explicit URL override, and behavior when the console is not running. It never starts a real console server or contacts a provider.

### Console page checks

From `console/`, using the exact script names in `console/package.json`:

```bash
npm test              # vitest run
npm run typecheck     # tsc --noEmit
npm run build         # vite build into dist/
npm run check:dist    # fails if dist/ does not match a fresh build
npm run axe           # accessibility check
npm run keyboard      # keyboard-only walkthrough
```

`npm run dev` serves the page against the fixtures in `console/fixtures/` with no router or console server running.

The native tests cover:

- exact Agent models, effort inheritance, and pinned efforts
- built-in Explore, Plan, and general-purpose inheritance
- schema-valid family aliases and exact resolved targets for built-in Agent calls
- profile, Fast, and extra-usage model guards
- plain OpenAI routing without the hybrid router
- both hybrid root directions through one loopback router
- pure and hybrid open-model roots through the same signed session router
- exact body forwarding on existing providers and strict local Chat Completions translation
- existing-provider byte streaming and bounded open-model SSE translation
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
- offline release selection, checksum, attestation, archive safety, confirmation, updater installation, and cached notice flows
- user-only SessionStart update notices with no network or model-context output
- line endings, release metadata, documentation links, and writing rules

## Bash launcher tests

```bash
bash tests/test-airlock.sh
bash tests/test-setup.sh
python tests/test-setup-pty.py
```

These tests replace Claude Code and provider commands with stubs. They verify old-config compatibility, saved OpenAI and hybrid roots, explicit overrides, exact pure and hybrid open-model selectors, no subscription-proxy dependency for a pure local root, update and version dispatch, provider Fast controls, the session-local `airlock fast -r` shortcut and `/airlock-fast` clean-exit handoff, paid Anthropic Fast refusal, exact Agent catalogs, worker-disabled local roots, allowed tools, model allowlists, full setup labels, worker effort inheritance and pins, the managed web tools server entry and its `AIRLOCK_WEB_TOOLS=off` switch, the per-profile denial of built-in web tools for non-Anthropic roots, the real `--mcp-config` file handed to Claude with its cleanup after the session ends, invalid input, and backups without using OAuth, a configured inference server, or model quota.

`test-setup-pty.py` runs the guided flow through a real POSIX terminal six times: a plain 80 column run, a color-capable run, a redirected-output run, a 40 column run, a color keyboard run, and a no-color keyboard run. It checks the ASCII wordmark and introduction, six numbered sections and progress track, full Claude and GPT names and IDs, recommended markers and Enter hints, honest effort wording, provider Fast choices and paid-credit wording, Claude Fable 5, separate Claude Code and Codex OAuth wording, the grouped review screen, hidden Advanced details, and saved hybrid defaults. The keyboard runs send real Up and Down escape sequences, prove wraparound and Enter selection, and keep number, name, and `?` input working. The suite also checks that plain or redirected streams contain no escape sequences, long macOS-style config paths use a stacked layout, and wrapped lines fit the terminal. Every run reports its start and finish. The harness polls the child independently of pipe EOF, then drains buffered output. A timeout terminates the full child process group within a fixed grace period, falls back to the exact child when the platform rejects a group signal, and reports the last output plus unsent key count. The suite prints a skip on Windows, where the Python PTY module is unavailable.

## macOS and Linux installer test

```bash
bash tests/test-install.sh
```

This test installs into a temporary folder with fake commands. It checks the installed file set and modes, including both open-model helpers, managed-file conflicts, missing Claude Code, automatic proxy installation, signed-out Codex OAuth with and without approved login, writable proxy config and state fallback selection, private directory modes, generated Homebrew service environment, the `airlock proxy auth` wrapper, separate Claude login reporting, absent, valid, and invalid open-model registry Doctor output, suppression of private local endpoint data, and doctor failure for an unhealthy proxy. It also covers the Console static-site same-parent swap, one-backup recovery, and fail-closed handling for unsafe or ambiguous backup state. Doctor never contacts the configured local endpoint. The new POSIX Console swap block ran under WSL. The full POSIX installer suite remains for native macOS and Linux CI because this Windows checkout's CRLF shebang blocked a later unrelated WSL check. The test exits without running on other systems, where `tests/test-windows.ps1` covers the same ground.

## Windows tests

From PowerShell:

```powershell
powershell -NoProfile -File .\tests\test-windows.ps1
```

The test installs into a temporary folder with fake commands. It checks PowerShell parsing, missing Claude Code and proxy refusal, managed-file conflicts, separate Claude login reporting, router, open-model helper, and worktree-hook installation, saved Claude and GPT hybrid roots, exact pure and hybrid open-model roots, current-policy resume with exact `-r` forwarding and a declared OpenRouter Agent in the native inline `--agents` payload, private file transport for managed settings and routing guidance, cleanup after Claude exits, and early refusal when the remaining command exceeds the Windows `CreateProcessW` limit. It also covers provider Fast startup and paid-usage refusal, explicit OpenAI override behavior, old-config compatibility, invalid profile refusal, doctor failure for an unhealthy proxy, and native Console static-site swap and backup recovery behavior. Its synthetic OpenRouter and open-model registries are credential-free, protected with the same Windows ACL writer used in production, and never connected to a real endpoint. The Python hybrid regression separately proves exact UTF-16 command-length accounting, cleanup on startup failure and interruption, and that an undeclared dynamic worker still fails closed.

`test-fast-session-end.py` covers malformed, oversized, non-clean, missing-channel, exact helper-command, and plugin metadata cases for the managed SessionEnd handoff hook. It uses fake temporary paths and does not start a session or contact a provider.

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

The script also syncs the marker's version with `MANAGED_BUNDLE_VERSION` in `bin/airlock-access.py`, so bumping the version needs no hand edit of the JSON. Review the marker change before committing it.

## Router protocol tests

`tests/test-router.py` uses local fake Anthropic and OpenAI servers with synthetic authorization values.

It verifies:

- exact request-body preservation outside documented provider compatibility normalization
- provider-specific header handling
- text-only custom-model notice normalization from a non-standard system-role message into top-level Anthropic `system` content
- exact OpenRouter provider, quantization, fallback, optional-parameter, and response-identity controls
- signed-snapshot-only open-model endpoint and route configuration
- fresh allowlisted Anthropic-to-Chat-Completions requests and translated text, tool, and usage responses
- no incoming credential, arbitrary header, proxy setting, private endpoint, or private model-identity leakage on local routes
- exact pre-commit and post-commit local response identity enforcement with sanitized failures
- endpoint-wide concurrency shared across route aliases, including release after errors and disconnects
- delayed local stream commitment, fragmented tool reassembly, usage-after-finish handling, and no local failover or compaction
- token-count routing
- unknown, malformed, and oversized request rejection
- redirect rejection with a sanitized upstream error
- retryable upstream failures surfacing as 502s that keep a bounded, printable reason
- deterministic redirects, credential failures, malformed responses, and OpenRouter identity mismatches surfacing as non-retryable 400s; identity mismatches retain only the bounded printable model identity needed for diagnosis
- OpenRouter requests stripping Anthropic server-side tools and matching history blocks before forwarding, with an opt-out for explicit compatibility testing
- same-category rate limit handoff on HTTP 429 and 529, with cooldown memory, Retry-After parsing, a three-hop cap, no upstream contact for cooling routes, honest sanitized 429s when every peer is limited, provider-pin removal when a hop leaves OpenRouter, snapshot chain validation, and diagnostics outcomes
- upstream status and error-body pass-through for statuses other than the failover codes
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

Live tests use subscription quota or contact a user-hosted inference process and can send repository content to a provider or local model. They are never part of CI, setup, or Doctor. `airlock open-model check ROUTE` is a real catalog request even though it sends no generation prompt.

Read [Live tests](live-tests.md) and get exact permission before running one. The permission must name provider, exact model, repository files, public web state, extra-usage state, Fast state, and worker count.

The guarded Sol context helper is not called by any offline test, setup, Doctor, or CI path. After installing the exact candidate and recording matching approval, run it manually:

```bash
python scripts/test-sol-long-context.py \
  --authorized-scope 'provider=openai;model=gpt-5.6-sol;repository-files=none;public-web=off;extra-usage=off;fast=off;workers=0'
```

It makes one root-only request from an empty temporary directory, sends an in-memory synthetic prompt through standard input, and prints only sanitized usage and marker booleans. Provider-reported input plus cache usage must exceed 300,000 tokens or the helper fails.
