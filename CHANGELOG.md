# Changelog

All user-facing changes are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow semantic versioning while the public interface is in beta.

## Unreleased

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

### Changed

- Plain `airlock` now uses native OpenAI Agents directly through the local OpenAI proxy.
- `airlock hybrid` now keeps both providers inside one Claude Code process through the temporary router.
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

### Security

- The hybrid router binds only to `127.0.0.1`, rejects redirects, and exits with its owner.
- The router does not log prompts, response bodies, headers, or credentials.
- The local router diagnostics endpoint keeps only bounded in-memory route, status, byte-count, duration, and outcome metadata.
- Claude authorization is forwarded opaquely only to Anthropic.
- Native worktree snapshots filter known credentials from tracked and eligible untracked files.
- Direct tool guards block known credential paths and high-confidence credential content.
- Installer and startup bundle checks include every native router and worktree safety component.

### Removed

- The `airlock-delegate`, `airlock-workflow`, `airlock-child`, and `airlock-check` helpers, their platform wrappers, and the delegate-only runtime module.
- The `airlock delegate` and `airlock workflow` subcommands and the `AIRLOCK_ENABLE_LEGACY_TRANSPORT` switch.
- Broker run state, descendant slots, and provider circuit breakers, which Claude Code's native Agent lifecycle now owns.

## 0.1.0-beta.1 - Planned

First public beta of Airlock, built from the MIT-licensed Claudex project.
