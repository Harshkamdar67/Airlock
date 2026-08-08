# Changelog

All user-facing changes are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow semantic versioning while the public interface is in beta.

## Unreleased

### Added

- Grok subscription path (Phase 1 of multi-provider work): `airlock grok`, hybrid roots `grok` / `composer`, workers `airlock-grok` and `airlock-composer`, router provider label `grok` (shared loopback proxy with Codex), and `airlock proxy grok auth` for Grok OAuth. Available on macOS, Linux, and Windows.
- Setup wizard support for Grok: a `grok` default profile, a Grok subscription question in hybrid, a Grok worker pool question, and `--grok-model` / `--grok-workers` flags.
- Doctor reports Grok OAuth on both platforms, and treats a missing Grok login as a failure only when the saved configuration enables Grok routes.

### Changed

- Grok routes are opt-in. A hybrid session gains Grok workers only when the saved configuration enables them or the root is itself a Grok model. Earlier work in this cycle enabled Grok for every hybrid session, which advertised workers to accounts without a Grok login.
- Hybrid provider-boundary guidance now names only the providers a session actually enabled, instead of always describing all three.
- A session that confirms the proxy is signed out of Grok disables the Grok routes rather than offering workers whose first request would fail. An unknown login state leaves the configured routes alone.

- Session guidance now names only the providers and workers a session actually enabled. A Grok-only session previously spent most of its guidance describing Luna armies and Anthropic workers it could not call.
- Grok and Composer now have explicit routing rules rather than only a descriptive role-map entry, so the orchestrator can positively select them. Composer is described as the agentic coding worker it is instead of a summarizer, and is eligible for automatic fan-out.

### Fixed

- `require_proxy_environment` hardcoded a `gpt-` root prefix, so the Windows launch path rejected every Grok-only session.
- The Windows launcher had no `grok` command, no Grok hybrid roots, and never passed the Grok agent catalogs to the access helper.
- `airlock.ps1` resolved every managed path from `$HOME\.config\airlock` while `install.ps1` honoured `AIRLOCK_CONFIG_DIR`, so a Windows install into a custom directory validated another installation's files.
- The installers refused to update any agent catalog whose contents changed, because catalogs carried no managed marker. They now carry one, and a file whose hash matches the installed bundle's record for that component is recognised as Airlock's own. A file with neither is still refused.
- `setup.sh` defaulted `AIRLOCK_HYBRID_MODEL` to `sol` while both launchers fall back to `sonnet`, so rewriting a config that never had the key silently moved the hybrid root. A new setup still recommends Sol.

## 0.1.0-beta.1 - 2026-08-05

First public beta of Airlock, built from the MIT-licensed Claudex project.

### Added

- A session-scoped loopback router for mixed OpenAI and Anthropic sessions.
- Exact model-ID routing for both hybrid root directions.
- True native Claude Code Agents for every enabled named worker.
- Exact allowed model overrides for built-in Explore, Plan, and general-purpose Agents.
- Native WorktreeCreate and WorktreeRemove hooks that preserve dirty tracked and eligible untracked context.
- Key-only env projections in native isolated worktrees.
- Credential-path, credential-bearing JSON, complete private-key, unsafe-link, size, and unstable-file filtering.
- Direct sensitive-file guards for Read, Grep, Glob, and Bash.
- Protocol tests for body preservation, header separation, streaming, redirects, unknown models, and router lifetime.
- Sanitized token-count observation in router diagnostics for both providers, read from responses that are already forwarded so streaming is unaffected.
- Native Windows router and worktree-hook installation coverage.
- Managed bundle coverage for the router and native worktree safety files.
- Plain-language documentation for native routing, exact Agent models, gateway limits, worktrees, security, and live parity.
- Declared effort levels for pinned GPT models so Claude Code's `/effort` command works on a GPT root, adjustable with `AIRLOCK_GPT_EFFORT_CAPABILITIES`.
- Saved default profiles and orchestrators, including explicit `airlock openai`, saved `airlock hybrid`, and the `airlock hybrid choose` picker.
- A guided terminal setup with full model names and IDs, worker presets, honest effort controls, a review screen, and optional Advanced settings.
- POSIX terminal coverage for the interactive setup flow and native Windows parity coverage for saved launch profiles.
- A branded, width-aware setup interface with clear progress, decision cards, and a grouped confirmation screen.
- Keyboard option selection with Up and Down arrows plus Enter, while number and name entry remain available.
- CODEOWNERS, safe issue forms, a pull request security checklist, and Dependabot updates for GitHub Actions.
- `airlock proxy auth` commands that preserve the proxy directory selected during setup.
- Provider-wide and provider-specific Fast controls for eligible OpenAI routes and native Claude Opus Fast startup, with paid Anthropic usage confirmation.
- A verified release-archive update guide for macOS, Linux, and Windows.
- `airlock version`, manual `airlock update --check` notices, confirmed `airlock update`, and explicit noninteractive `airlock update --yes` on every platform.

### Changed

- New setup configurations make bare `airlock` start the saved hybrid root, with GPT-5.6 Sol as the recommended orchestrator. `airlock openai` and explicit OpenAI aliases remain direct OpenAI-only launches. Existing configs without the new profile key keep their original OpenAI-only bare command.
- `airlock hybrid` now starts the saved hybrid root, while `airlock hybrid choose` opens the full seven-model picker.
- Hybrid profiles keep both providers inside one Claude Code process through the temporary router.
- Named `airlock-*` Agents now bind exact full model IDs.
- Named Agents inherit Claude Code's normal subagent tool pool instead of using transport-only Write and Bash tools.
- Named `airlock-*` workers now follow the session effort by default, so `/effort` changes the main model and its workers together in the middle of a session. Pin one worker with `AIRLOCK_EFFORT_<ROUTE>` or all of them with `AIRLOCK_WORKER_EFFORT`.
- The default session effort is now `high` instead of `xhigh`.
- The optional `airlock-worker` agent also follows the session effort unless `AIRLOCK_SUBAGENT_EFFORT` names a level.
- Built-in Explore inherits the orchestrator by default and can receive one exact allowed model ID for a call.
- Native Agent cards, background execution, cancellation, worktrees, and usage replace broker-rendered lifecycle state in normal sessions.
- Automatic fan-out remains Luna-only and final synthesis stays with a stronger model.
- Top-level Agent spawn depth remains one and named Agents cannot recurse.
- Hybrid mode preserves saved Claude subscription login and rejects explicit Anthropic credential overrides.
- OpenAI usage refresh and Anthropic `/usage` guidance remain separate.
- Substantial visual and interaction redesigns now require the eligible Opus worker in hybrid sessions instead of being treated as generic coupled implementation.
- Mixed requests are split by skill automatically, so UI/UX judgment and separable systems work go to their strongest eligible routes before root integration.
- Doctor reports Claude login and Codex OAuth separately, while keeping signed-out OpenAI-only behavior clear.
- GitHub workflows pin third-party Actions to full commit hashes and avoid persisting checkout credentials.
- Budget mode now turns both provider Fast controls off as well as blocking extra usage and automatic failover.
- Release tags must match the project version and point to a commit already on `main` before archives can be published.

### Fixed

- Long Agent responses no longer depend on a shell wrapper with a shorter foreground timeout.
- Claude authorization and OAuth capability headers are stripped before every OpenAI proxy request.
- Hybrid root selection now fails when the exact model is not enabled for the active route policy.
- Hybrid routing now recognizes the deterministic GPT wire ID Claude Code creates by removing the `[1m]` suffix from an enabled model.
- Built-in Agent model overrides now reject aliases, disabled models, cross-profile IDs, blocked extra-usage routes, and ineligible Fast routes.
- Dirty tracked and eligible non-ignored untracked files are available in native isolated worktrees without changing the main checkout or index.
- Changed or committed managed worktrees are preserved instead of removed.
- Broad file searches no longer return raw sensitive env or credential data.
- Unavailable GPT model discovery is now documented instead of being presented as a routing failure.
- Public web routing now avoids assigning Web Search to `xhigh` or `max` workers when the internal search model supports only `high` or below.
- Setup automatically uses `~/.airlock` when the normal macOS or Linux config parent is not writable, and every installed component resolves the same fallback without `sudo`.
- Long unbreakable config paths use a stacked review layout instead of overflowing the label column.
- Codex OAuth and proxy startup now use a private writable config or state fallback when the upstream default parent is blocked, without moving an existing healthy login.
- POSIX PTY tests now poll child exit independently of pipe EOF, use bounded process-group cleanup with a child fallback, and report the exact run instead of leaving macOS CI waiting for the job timeout.

### Security

- The hybrid router binds only to `127.0.0.1`, rejects redirects, and exits with its owner.
- The router does not log prompts, response bodies, headers, or credentials.
- The local router diagnostics endpoint keeps only bounded in-memory route, status, byte-count, duration, and outcome metadata.
- Claude authorization is forwarded opaquely only to Anthropic.
- Native worktree snapshots filter known credentials from tracked and eligible untracked files.
- Direct tool guards block known credential paths and high-confidence credential content.
- Installer and startup bundle checks include every native router and worktree safety component.
- Pull request workflows use read-only permissions, immutable Action commits, non-persistent checkout credentials, and bounded job timeouts.
- Release jobs target a protected approval environment, refuse tags outside `main`, verify the full offline suite, publish checksums, and attach GitHub build provenance.
- The updater requires an exact release checksum, restricts downloads and redirects to GitHub, verifies attestations when GitHub CLI is present, rejects unsafe archives, and delegates installation to managed-file conflict checks.

### Removed

- The `airlock-delegate`, `airlock-workflow`, `airlock-child`, and `airlock-check` helpers, their platform wrappers, and the delegate-only runtime module.
- The `airlock delegate` and `airlock workflow` subcommands and the `AIRLOCK_ENABLE_LEGACY_TRANSPORT` switch.
- Broker run state, descendant slots, and provider circuit breakers, which Claude Code's native Agent lifecycle now owns.
