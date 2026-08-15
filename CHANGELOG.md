# Changelog

All user-facing changes are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow semantic versioning while the public interface is in beta.

## Unreleased

## 0.1.0-beta.4 - 2026-08-15

### Added

- A session-local OpenAI Fast handoff. `airlock fast -r` starts one `gpt-5.6-sol-fast` root through the Codex proxy without changing saved `AIRLOCK_OPENAI_FAST`. Inside a managed Airlock session, `/airlock-fast` arms the current session; a clean exit lets its owning launcher resume the exact conversation once on fixed `gpt-5.6-sol-fast`. This is not Claude Code Anthropic `/fast`. Eligible OpenAI plan and proxy checks still apply, with no fallback. Hard kills, crashes, non-clean exits, hook failures, and expiry intentionally prevent relaunch. The private handoff marker is nonce-, PID-, cwd-, and session-bound and carries no credentials, prompts, transcripts, arbitrary executable data, or model choice. The installed managed plugin SessionEnd hook is required and is authorized as part of the Airlock plugin, not global hooks.

- An optional, opt-in OpenRouter path for hybrid sessions. Nothing about it turns on by itself: `airlock openrouter auth set-key` stores an OpenRouter API key using the operating system's own credential protection (the macOS Keychain, Windows DPAPI scoped to the current Windows user, or the Linux Secret Service through `secret-tool`), with no plaintext file fallback, and `airlock openrouter models add ROUTE MODEL ENDPOINT` declares one exact routable model that is not an alias and one exact serving endpoint in a small local registry after checking both catalog identities against OpenRouter's public catalog and confirming by name.
- `airlock openrouter auth status` and `airlock openrouter auth logout`, and `airlock openrouter models list`, `remove`, and `refresh`, for managing the stored key and the declared routes. `refresh` reports what changed against the public catalog and only saves it with `--apply`. The endpoint tag, catalog provider name, provider-registry routing slug, endpoint quantization, and separate canonical slug are frozen and must not drift during refresh. Concurrent registry updates fail with a rerun message instead of silently losing a change.
- Three curated, opt-in OpenRouter presets for Kimi K3, DeepSeek V4 Flash 0731, and Qwen 3.6 27B. `airlock openrouter models presets` lists them offline, and `airlock openrouter models add-preset NAME` expands one into an exact model, frozen canonical provenance, default route, and pinned endpoint before running the normal confirmed public catalog checks. Setup and installation do not enable any preset.
- Matching preset workers receive fixed, clearly labeled community-derived suggested-use guidance in their Agent descriptions. The guidance is unverified, is not a capability, price, or availability guarantee, and never enters registry JSON, session snapshots, route policy, or Agent prompts. Airlock does not accept custom prompt text or descriptions through these commands.
- A declared route appears as a named `airlock-or-ROUTE` Agent inside a hybrid session, never through OAuth. Every request constrains OpenRouter to the verified provider-registry slug and endpoint quantization with fallback routing turned off. Provider plus quantization must identify one listed endpoint, and a response is accepted only for the exact routable or frozen canonical model identity. Both the model and endpoint must report `tools` and `tool_choice` support. OpenRouter's `count_tokens` operation is not available on these routes.
- `airlock opr [ROUTE] [Claude arguments...]`, an exclusive OpenRouter-only root command. It starts a session whose root is exactly one declared registry route, named directly or picked from an offline interactive list of declared routes; outside an interactive terminal, or for an unknown, disabled, or misspelled route, it fails closed instead of guessing. `--model`/`-m` cannot be forwarded, because the root is selected by exact registry route rather than a typed model ID. An `opr` session has no dependency on Claude, Codex, or Grok credentials and does not start the OpenAI subscription proxy. It has no saved default: every launch names its route or asks, and it does not change guided setup or any saved profile.
- Because Airlock does not verify a declared model's real capability, context window, or cost, an OpenRouter Agent is normally treated as extra usage and needs the same `Extra usage authorized: yes` confirmation as any other extra-usage worker under the default `ask` policy. The one route explicitly selected as the `airlock opr` root is the exception: it carries that session's normal traffic and is not itself gated by the extra-usage policy, the same as any other explicit session root. Any other declared route that becomes available in that same `opr` session still follows the normal extra-usage policy.
- A declared route's verified metadata expires after 30 days. Run `airlock openrouter models refresh --apply` before it expires; an expired or otherwise invalid registry file stops every Airlock session, not only ones using OpenRouter, until it is refreshed or the affected route is removed.

### Changed

- OpenRouter catalog verification now accepts exact model entries whose `alias_target` field is omitted or explicitly null, matching current catalog responses, while still rejecting every declared non-null alias target and all dynamic model selectors.
- The exclusive OpenRouter-only root command was briefly documented as `airlock orp`. That spelling is rejected; use `airlock opr` instead.

### Fixed

- Windows sessions no longer place generated routing guidance and managed settings JSON directly on Claude Code's command line. They use private session files for those two values, keep exact Agent JSON inline because Claude Code has no Agent-file option, clean the files after exit, and reject any remaining command that exceeds the `CreateProcessW` limit before starting the router or Claude. This fixes `[WinError 206]` for `airlock hybrid sol -r` with the complete managed worker catalog and enabled OpenRouter routes.
- OpenRouter routes now preserve Claude Code's custom-model notice while moving its non-standard `messages[].role = "system"` entry into the Anthropic Messages API's top-level `system` field. Strict endpoints such as the pinned Qwen 3.6 27B Chutes route no longer reject the native Agent request with HTTP 400, while exact provider and quantization pinning, disabled fallback, tools, thinking, cache controls, metadata, and response-model validation remain unchanged.
- Windows hybrid resume now carries the same current validated policy through Agent rendering, exact permission generation, and managed session settings. `airlock hybrid sol -r` therefore accepts only currently declared OpenRouter workers instead of rejecting them as unexpected, while stale, invalid, or undeclared routes still fail closed.
- The curated Kimi K3 preset now pins the uniquely identifiable DigitalOcean endpoint instead of Morph, whose current catalog entries can no longer be distinguished by provider and quantization alone.
- OpenRouter endpoint tags such as `deepinfra/fp4` are no longer sent as provider names. Airlock now resolves the catalog provider name through OpenRouter's provider registry, routes with its exact slug plus endpoint quantization, rejects ambiguous mappings, and accepts only the exact routable or frozen canonical response identity.
- OpenRouter requests no longer require an endpoint to advertise every optional field in Claude Code's native Messages payload. Exact provider and quantization constraints, disabled fallback, required `tools` and `tool_choice` catalog support, and exact response-model validation remain enforced.
- `airlock session-usage` now omits OpenRouter summary groups instead of rejecting the whole report after an OpenRouter Agent runs. Anthropic, OpenAI, and Grok totals remain available, and the text view states that OpenRouter belongs to a separate account.
- Curated community guidance now appears only when a declared route matches the preset's exact model, pinned endpoint, and frozen canonical provenance. A manual route that reuses only the model ID no longer inherits guidance researched for another endpoint or model snapshot.
- Windows now streams OpenRouter helper output directly and preserves each helper's exact exit status, so confirmation questions are visible before the helper waits for an answer instead of appearing to hang.
- Windows sessions run by an elevated administrator now clean up their policy snapshot. Windows gives files created by an administrator to the built-in Administrators group rather than to the account itself, which failed Airlock's owner check and left the snapshot in the session runtime directory after exit. The session snapshot and the Fast handoff marker now carry an explicit private owner and access list, matching how saved Airlock state is already written.
- The POSIX launcher now preserves its original standard input explicitly when keeping Claude as a background child, and treats deliberately closed input as end-of-file, rather than depending on incidental Bash redirection state for the interactive TUI.

## 0.1.0-beta.3 - 2026-08-09

### Added

- An explicit `airlock update --check` that finds a newer release now saves a bounded local reminder. A later Airlock startup, resume, or clear shows the available version as a user-only message without contacting GitHub or adding the notice to model context. Notices expire after seven days, and missing, malformed, unsafe, or incompatible state is ignored silently.

## 0.1.0-beta.2 - 2026-08-09

### Added

- Grok subscription path (Phase 1 of multi-provider work): `airlock grok`, hybrid roots `grok` / `composer`, workers `airlock-grok` and `airlock-composer`, router provider label `grok` (shared loopback proxy with Codex), and `airlock proxy grok auth` for Grok OAuth. Available on macOS, Linux, and Windows.
- Setup wizard support for Grok: a `grok` default profile, a Grok subscription question in hybrid, a Grok worker pool question, and `--grok-model` / `--grok-workers` flags.
- Doctor reports Grok OAuth on both platforms, and treats a missing Grok login as a failure only when the saved configuration enables Grok routes.
- `airlock session-usage` reports cumulative provider-reported request and token totals from the active hybrid router without rewriting responses or inventing missing counts.
- An opt-in Sol long-context helper sends one synthetic root-only request through standard input and fails unless provider-reported input plus cache usage exceeds 300,000 tokens and both distant markers return.

### Changed

- Grok routes are opt-in. A hybrid session gains Grok workers only when the saved configuration enables them or the root is itself a Grok model. Earlier work in this cycle enabled Grok for every hybrid session, which advertised workers to accounts without a Grok login.
- Hybrid provider-boundary guidance now names only the providers a session actually enabled, instead of always describing all three.
- A session that confirms the proxy is signed out of Grok disables the Grok routes rather than offering workers whose first request would fail. An unknown login state leaves the configured routes alone.
- Every canonical OpenAI/GPT model ID is now bare. Legacy GPT IDs ending in `[1m]` remain accepted at launcher, setup, and saved-config input boundaries and normalize immediately; native Claude Opus 5, Sonnet 5, and Fable 5 retain `[1m]`.
- Session guidance now names only the providers and workers a session actually enabled. A Grok-only session previously spent most of its guidance describing Luna armies and Anthropic workers it could not call.
- Routine Explore now uses the schema-valid `haiku` family slot instead of silently inheriting a different premium root. Airlock resolves all four built-in Agent family slots to exact models enabled for the active session, and named `airlock-*` Agents remain the exact-model interface.
- Native Anthropic roots now leave Claude Code's process-wide auto-compact override unset. The authorized Sol proof above 300,000 tokens did not pass, so Sol now uses the honest bare ID `gpt-5.6-sol` and OpenAI/Grok roots retain the saved conservative fallback. An explicitly exported `CLAUDE_CODE_AUTO_COMPACT_WINDOW` or `AIRLOCK_CONTEXT_WINDOW` still wins for the whole process.
- Grok and Composer now have explicit routing rules rather than only a descriptive role-map entry, so the orchestrator can positively select them. Composer is described as the agentic coding worker it is instead of a summarizer, and is eligible for automatic fan-out.

### Fixed

- Claude Code's Agent input accepts only the `fable`, `opus`, `sonnet`, and `haiku` family values, while Airlock previously told built-in Explore to retry with an exact GPT ID that the tool schema rejected. Every pure and hybrid profile now owns all four family slots, validates each exact target against the active policy, reserves Haiku for economical discovery, and labels each slot with the model it will call.
- The setup PTY tests now include the Grok subscription question in the recommended hybrid path, so Linux and macOS CI reaches and applies the final review instead of running out of keystrokes.
- Git Bash on Windows no longer lets the hybrid router lose its owner when the launcher starts Claude. The launcher stays alive, forwards terminal signals, returns Claude's exact status, and shuts the router down after Claude exits.
- Grok-only sessions inherited the global OpenAI utility model as `ANTHROPIC_SMALL_FAST_MODEL`, so Claude Code could silently send background work to GPT from a profile that promised to stay on Grok. The utility slot is now Composer or the enabled Grok fallback.
- The Windows `airlock models` help omitted the Grok-only command and both Grok root aliases even though the launch paths were installed and working.
- Claude Opus 5, Sonnet 5, and Fable 5 ran at 200000 tokens instead of their native 1000000. Claude Code grants those models their full window only while `ANTHROPIC_BASE_URL` is unset or points at `api.anthropic.com`, and Airlock always points it at the session router, so every Claude root and Claude worker silently lost four fifths of its context and compacted far more often than the same model outside Airlock. All three are now requested as `claude-opus-5[1m]`, `claude-sonnet-5[1m]`, and `claude-fable-5[1m]`, which Claude Code honours from behind the router. Claude Haiku 4.5 is genuinely a 200000 token model and is unchanged.
- The router allow-list derived a suffix-free wire ID for OpenAI models only, which was correct only while OpenAI was the only provider whose IDs carried `[1m]`. It now applies to every provider, so an Anthropic worker no longer fails closed against its own route.
- `require_proxy_environment` hardcoded a `gpt-` root prefix, so the Windows launch path rejected every Grok-only session.
- The Windows launcher had no `grok` command, no Grok hybrid roots, and never passed the Grok agent catalogs to the access helper.
- `airlock.ps1` resolved every managed path from `$HOME\.config\airlock` while `install.ps1` honoured `AIRLOCK_CONFIG_DIR`, so a Windows install into a custom directory validated another installation's files.
- The installers refused to update any agent catalog whose contents changed, because catalogs carried no managed marker. They now carry one, and a file whose hash matches the installed bundle's record for that component is recognised as Airlock's own. A file with neither is still refused.
- `setup.sh` defaulted `AIRLOCK_HYBRID_MODEL` to `sol` while both launchers fall back to `sonnet`, so rewriting a config that never had the key silently moved the hybrid root. A new setup still recommends Sol.
- Earlier context handling applied the saved 272000 fallback to every root, including native Anthropic roots that Claude Code could size itself. Native Anthropic roots now stay model-aware. OpenAI and Grok roots keep the fallback because the guarded Sol request above 300,000 tokens exited with status 1 and did not produce a valid proof.
- `AIRLOCK_CONTEXT_WINDOW` accepted values Claude Code discards. Claude Code takes 100000 to 1000000 and ignores anything else without a word, so an out-of-range number looked applied while doing nothing. Both launchers now reject it before the session starts. The Windows launcher previously checked only that the value was a positive integer, and the POSIX launcher did not check at all.
- Router diagnostics never reported token usage for Anthropic routes. Anthropic answers with `Content-Encoding: gzip`, and the observer read the compressed bytes, so it could never find a usage object. It now decodes a private copy of the response to read the counts. The bytes forwarded to the client are still passed through untouched, and a response in an encoding the standard library cannot read records no usage instead of guessing.

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
