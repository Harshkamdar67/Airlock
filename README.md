# Airlock

**Don't pick a side.** Run OpenAI and Anthropic models together in one Claude Code session, with credentials that never cross.

[![Tests](https://github.com/Harshkamdar67/Airlock/actions/workflows/test.yml/badge.svg)](https://github.com/Harshkamdar67/Airlock/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0--beta.7-orange.svg)](CHANGELOG.md)
[![Platforms](https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](docs/windows.md)

An airlock is a chamber where two environments meet without mixing. That is the whole idea here. A GPT worker and a Claude worker can run side by side in the same session, and neither one ever sees the other's login.

Airlock keeps Claude Code's terminal, tools, permissions, hooks, Agent cards, background work, cancellation, worktrees, and usage display. It adds a local OpenAI path through [`claude-code-proxy`](https://github.com/raine/claude-code-proxy) and a small local router for mixed-provider sessions.

```bash
airlock                 # Saved default, new setups recommend the auto hybrid root
airlock openai          # Saved OpenAI-only root, no mixed-provider router
airlock fast -r         # One-session gpt-5.6-sol-fast root, without saving Fast mode
airlock hybrid opus     # Claude Opus drives, GPT workers available
airlock hybrid sol      # GPT Sol drives, Claude workers available
airlock hybrid ox-alpha # A declared OpenRouter route drives the mixed session
airlock grok            # Saved Grok-only root, needs a separate Grok login
airlock status          # Current session root and recent router actions
```
> [!NOTE]
> This project is in beta. It does not replace Claude Code, Codex, or `claude-code-proxy`. The mixed-provider gateway works, but Anthropic does not officially support non-Claude models behind a Claude Code gateway. Read [Known limits](#known-limits) before relying on it for important work.

## Why use it

- Keep normal `claude` and `codex` unchanged.
- Start with an OpenAI or Anthropic model, and reach both from the same session.
- Use real Claude Code Agents for both providers, each with an exact model, and move them all with `/effort`.
- Keep native Agent cards, tools, background work, cancellation, and worktrees.
- Let Plan and general-purpose inherit the main model, route routine Explore through the economical `haiku` family slot, or choose an exact named worker for one call.
- Set a budget mode and a smaller worker cap when you want one.
- See OpenAI plan usage without making a model request.
- Keep ignored files, env values, and known credentials out of isolated worker copies.

## How it works

```text
OpenAI-only profile

Claude Code
    |
    `-- 127.0.0.1 OpenAI proxy
            `-- OpenAI subscription models

Hybrid profile

Claude Code
    |
    `-- 127.0.0.1 session router
          |-- exact gpt-* model ID --> local OpenAI proxy
          `-- exact claude-* ID   --> Anthropic
```

The setup wizard saves what bare `airlock` starts. New setups recommend the hybrid profile with the reserved root `auto`, which resolves at launch to Fable when it is included on your plan, otherwise Opus, otherwise Sonnet, and never picks a model that needs confirmed extra usage. `airlock openai` always starts the saved OpenAI-only root, and an explicit shortcut such as `airlock terra` also stays OpenAI-only. `airlock grok` starts the saved Grok-only root. Existing configs without a saved profile keep the original OpenAI-only bare command.

Grok is off until you turn it on. It runs through the same local proxy as Codex but signs in separately, so a hybrid session gains Grok workers only when your saved configuration enables them or when you name a Grok root directly. Run `airlock proxy grok auth login` first, then either answer the Grok question in `./scripts/setup.sh` or pass `--grok-workers grok,composer`.

The hybrid profile starts one temporary router on `127.0.0.1`. The router sends exact enabled Claude model IDs to Anthropic and exact enabled bare GPT model IDs to the local OpenAI proxy. Claude Code removes the `[1m]` context suffix before a request goes out for supported native Claude models, so the router registers that one deterministic wire form alongside each enabled Claude full ID that carries the suffix. Claude Code still owns every Agent call and tool event.

The router never sends Claude authorization headers to the OpenAI proxy. It forwards saved Claude login authorization opaquely only to Anthropic. It does not read credential files or log prompts, responses, or headers. Its local diagnostics endpoint keeps only a bounded in-memory list of model, provider, status, byte-count, duration, outcome, and token-count metadata.

[Read the full explanation](docs/how-it-works.md).

## Install

### macOS and Linux

You need:

- [Claude Code](https://code.claude.com/docs/en/setup)
- Homebrew
- Git
- Python 3
- curl
- A ChatGPT plan with Codex access
- Optional: a Grok plan, only if you want the Grok routes

Clone this repository and run:

```bash
./scripts/setup.sh
```

The guided terminal setup shows full provider and model names, exact IDs, roles, and relative usage. Use the Up and Down arrow keys plus Enter, or keep typing a number or name. Its six short sections cover the default profile and orchestrator, worker pool, effort behavior, safety limits, installation actions, and a final review screen. Nothing on the machine changes until you accept that screen. If the normal macOS or Linux config parent is not writable, Airlock uses `~/.airlock` automatically instead of asking for `sudo`. If the upstream proxy's normal config or state parent is also blocked, setup gives the proxy a private writable fallback and uses it consistently for OAuth, the service, the launcher, and Doctor.

Provider Fast startup is part of safety and budget. Background-command, utility, Luna swarm selection, failover, capacity, generic-worker, and per-model controls stay under Advanced.

For the tested defaults without the wizard:

```bash
./scripts/install.sh --with-agent
```

### Windows

You need:

- [Claude Code](https://code.claude.com/docs/en/setup)
- [Git for Windows](https://git-scm.com/download/win), including Git Bash
- Python 3
- `claude-code-proxy`. The Windows installer downloads Airlock's carried build of [`claude-code-proxy`](https://github.com/Harshkamdar67/claude-code-proxy) when none is installed and verifies its checksum. Stock releases open browser login through `cmd start`, which truncates OAuth URLs at the first ampersand and breaks Grok login; see [Troubleshooting](docs/troubleshooting.md).
- A ChatGPT plan with Codex access

Complete the official proxy login in an interactive terminal:

```powershell
claude-code-proxy codex auth login
```

Then install and check it:

```powershell
powershell -NoProfile -File .\scripts\install.ps1
powershell -NoProfile -File .\scripts\doctor.ps1
```

The installer does not change PATH, native Claude settings, native Codex settings, global hooks, registered plugins, or MCP settings. Add `%USERPROFILE%\.local\bin` to your user PATH if `airlock` is not found.

[Read the Windows guide](docs/windows.md). After installation, use `airlock update --check` to query GitHub manually and `airlock update` for a confirmed, verified update. A successful check for a newer release leaves a short local notice for the next Airlock session. Normal startup only reads that bounded cache and never checks GitHub. Existing configuration is preserved, and only recognized managed files are replaced. [Read the update guide](docs/updating.md).

## First run

Run `airlock` to start the saved profile, or name a root directly with `airlock openai`, `airlock fast -r`, `airlock grok`, `airlock hybrid opus`, or `airlock hybrid choose`. [Common commands](#common-commands) lists the full set.

`airlock fast -r` starts one `gpt-5.6-sol-fast` session through the Codex proxy without changing saved `AIRLOCK_OPENAI_FAST`; eligible OpenAI plan and proxy checks apply, with no fallback.
In a managed session, `/airlock-fast` arms a one-shot handoff. Exit cleanly and the owning launcher resumes the exact conversation once on fixed `gpt-5.6-sol-fast`. This is not Claude Code Anthropic `/fast`; hard kill, crash, non-clean exit, hook failure, or expiry prevents relaunch. The private nonce-, PID-, cwd-, and session-bound marker carries no credentials, prompts, transcript data, arbitrary executable data, or model choice. The managed plugin SessionEnd hook is required and is not a global hook.

Inside the session, ask for work normally. The main model can use:

- built-in Explore for read-only repository discovery
- built-in Plan for read-only technical design
- built-in general-purpose for multi-step work
- exact named workers such as `airlock-luna`, `airlock-sol`, `airlock-opus`, and `airlock-sonnet`, plus `airlock-grok` and `airlock-composer` when Grok is enabled

Plan and general-purpose inherit the orchestrator when the Agent call omits `model`. Routine Explore should use `model="haiku"`, which Airlock resolves to the exact economical discovery model named in the session guidance. If an unpinned Explore would spend a different premium root, the guard gives that schema-valid retry. Explore, Plan, and general-purpose accept Claude Code's `fable`, `opus`, `sonnet`, and `haiku` family aliases; Airlock validates each resolved target against the active profile. Use a named `airlock-*` worker when exact model identity matters.

Named `airlock-*` workers have a fixed exact model. Their effort follows the session by default, so `/effort` moves the main model and those workers together. You can pin one route or every worker in the config. Claude Code does not expose per-call Agent effort for either Claude or OpenAI workers, so Airlock cannot yet vary one worker's effort task by task. Automatic task-specific effort routing is under design. Until it is available, use session `/effort` or setup-time pins. Named workers use Claude Code's normal subagent tools.

## How work is routed

The main model starts with the smallest useful approach:

1. Work directly for small, connected, or already understood work.
2. Use Explore for bounded read-only discovery.
3. Use Plan after the relevant code is known.
4. Use general-purpose for multi-step work that should stay with one model.
5. Use one exact named worker when another model is a better fit.
6. Use several Luna Agents only for independent high-volume work.
7. Keep integration and final synthesis with a stronger main model, Sol, or Opus.

Automatic armies use only the economical high-volume routes: Luna, eligible Luna Fast, and Grok Composer. Sol, Terra, Opus, Sonnet, Fable, and Haiku are never multiplied automatically. For UI and UX work, an exact user choice wins; otherwise visual direction prefers Opus and bounded component work fits Sonnet.

[Read the detailed routing guide](docs/how-it-works.md).

## Common commands

```bash
airlock                         # saved default profile and orchestrator
airlock hybrid                  # saved hybrid orchestrator
airlock hybrid choose           # full interactive hybrid picker
airlock openai                  # saved OpenAI-only orchestrator
airlock grok                    # saved Grok-only orchestrator
airlock opr                     # interactive OpenRouter-only root picker
airlock mode                    # show routing and worker limits
airlock status                  # this session's root and recent router actions
airlock usage                   # refresh stale OpenAI usage and show it
```

Every root alias, policy, settings file, and environment variable is listed in [Capabilities and commands](docs/commands.md).

Every command and setting is listed in one place in [Capabilities and commands](docs/commands.md).

## Handoff when a model is unavailable

When a provider answers 402, 429, or 529, Airlock retries the same request on another enabled model in the same usage category rather than failing the turn, and it does the same when a peer rejects a conversation for being too large.

A handoff is never silent. Claude Code sends one request and receives one answer, so on its own it cannot tell a different model replied. At the end of any turn where the router switched, a notice names the model that was unavailable and the one that answered, and the replacement is told it is standing in, so it does not answer in the other model's name.

Two limits: an Anthropic rate limit is forwarded to Claude Code untouched, because Claude Code already handles its own provider's limits (`AIRLOCK_ANTHROPIC_RATE_LIMIT=handoff` switches models instead); and `/model` cannot show a handed-off model, because a handoff is per request, not per session. `airlock status` is the accurate record. [Commands and settings](docs/commands.md#handoff).

`airlock status` reports failover, cooldown skips, effort clamps, and safe error replacement without showing prompts, provider error bodies, headers, or credentials. New provider models do not wait on a release: declare them in your own [`models.json`](docs/models-and-usage.md#declaring-your-own-models).

Use native Claude Code's `/usage` screen for Anthropic subscription bars; Anthropic documents no personal subscription API that Airlock can safely read. OpenRouter is a separate opt-in path that stays off until you store a key and declare a route. A declared route becomes an `airlock-or-ROUTE` worker in a hybrid session, and `airlock opr [ROUTE]` starts an OpenRouter-only session on that one route with no other provider credential involved. Preset suggestions come from community reports, are unverified, and are not capability, price, or availability guarantees. [How it works](docs/how-it-works.md#openrouter-routes-optional) | [Models and usage](docs/models-and-usage.md)

## Security and file access

When the main model requests `isolation: "worktree"`, the session-scoped WorktreeCreate hook builds a clean synthetic snapshot from:

- current tracked files and tracked changes
- eligible non-ignored untracked regular files
- key names only from tracked or eligible env files

It leaves out:

- known credential paths
- JSON files with known credential fields
- complete private-key blocks
- ignored files
- unsafe links and Windows reparse points
- special files and outside-repository paths
- content above the documented safety limits

The snapshot does not stage, reset, clean, commit to, or change the user's branch, index, or working files. Claude Code owns the Agent and worktree lifecycle. A changed worktree is preserved rather than deleted by the custom cleanup hook.

The same session plugin blocks direct Read, Grep, Glob, and obvious Bash access to sensitive env and credential files. These checks are not a full operating-system sandbox. Do not commit real credentials or run untrusted repository code without stronger isolation.

Read [Security](SECURITY.md) and the [threat model](docs/threat-model.md).

## Known limits
- Anthropic supports Claude Code gateways and saved-login forwarding, but it does not officially support non-Claude models behind a gateway.
- GPT model IDs may not appear in Claude Code's `/model` discovery list. Start the exact root with `airlock` or `airlock hybrid`, use Claude Code's family aliases for built-in Agents, and use a named `airlock-*` Agent when exact model identity matters.
- Remote Control is unavailable when Claude Code uses a non-Anthropic base URL.
- Native Claude Code decides which tools subagents can use. Airlock cannot add a tool that Claude Code itself excludes from subagents.
- Native Agent cards can report zero tokens for custom OpenAI and Grok IDs even when the provider returned usage. In a hybrid session, `airlock session-usage` shows the router's cumulative Anthropic, OpenAI, and Grok provider-reported totals without changing provider responses; OpenRouter is omitted because it belongs to a separate account. It is not a bill. Because `CLAUDE_CODE_AUTO_COMPACT_WINDOW` is process-wide, native Anthropic roots stay uncapped and other OpenAI and Grok roots keep the saved conservative fallback; explicit user overrides still win for every worker.
- File hooks and prompts do not replace an operating-system sandbox.
- Grok routes share the local proxy with Codex but need their own login, and Airlock cannot read Grok plan windows, so `airlock usage` covers OpenAI only.
- OpenRouter routes are entirely user-declared. Airlock checks the exact model and endpoint against the public catalog when you add or refresh one, but it does not verify or rank a route's real capability, context window, or cost, and it does not offer `count_tokens` for those routes.
- Subscription access, provider terms, model availability, and usage limits can change.

## Documentation

- [Getting started](docs/getting-started.md)
- [Capabilities and commands](docs/commands.md)
- [How it works](docs/how-it-works.md)
- [Models, limits, and usage](docs/models-and-usage.md)
- [Windows](docs/windows.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Testing](docs/testing.md) | [Live tests that use plan quota](docs/live-tests.md) | [Threat model](docs/threat-model.md)
- [Contributing](CONTRIBUTING.md) | [Code of Conduct](CODE_OF_CONDUCT.md) | [Support](SUPPORT.md) | [Release process](docs/releasing.md) | [Changelog](CHANGELOG.md)

## What this project does not do

- It does not modify native Claude Code or native Codex.
- It does not read, copy, or decode login token files.
- It does not make subscription limits unlimited or silently switch an exact model to another provider.

## Credits

The OpenAI connection is powered by [`raine/claude-code-proxy`](https://github.com/raine/claude-code-proxy). That project handles OpenAI OAuth, request translation, streaming, and tool calls.

Airlock grew from the MIT-licensed Claudex project by Miguel Torrez. The original license and credit are preserved.

See [CREDITS.md](CREDITS.md) for the full list.

## License

This repository is released under the [MIT License](LICENSE). `claude-code-proxy` is separate. Claude Code, Codex, GPT, ChatGPT, and OpenAI are respective Anthropic and OpenAI products; Airlock is independent.
