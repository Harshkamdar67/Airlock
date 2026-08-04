# Airlock

**Don't pick a side.** Run OpenAI and Anthropic models together in one Claude Code session, with credentials that never cross.

[![Tests](https://github.com/Harshkamdar67/Airlock/actions/workflows/test.yml/badge.svg)](https://github.com/Harshkamdar67/Airlock/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0--beta.1-orange.svg)](CHANGELOG.md)
[![Platforms](https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey.svg)](docs/windows.md)

An airlock is a chamber where two environments meet without mixing. That is the whole idea here. A GPT worker and a Claude worker can run side by side in the same session, and neither one ever sees the other's login.

Airlock keeps Claude Code's terminal, tools, permissions, hooks, Agent cards, background work, cancellation, worktrees, and usage display. It adds a local OpenAI path through [`claude-code-proxy`](https://github.com/raine/claude-code-proxy) and a small local router for mixed-provider sessions.

```bash
airlock hybrid opus     # Claude Opus drives, GPT workers available
airlock hybrid sol      # GPT Sol drives, Claude workers available
airlock                 # OpenAI only, no router
```

> [!NOTE]
> This project is in beta. It does not replace Claude Code, Codex, or `claude-code-proxy`. The mixed-provider gateway works, but Anthropic does not officially support non-Claude models behind a Claude Code gateway. Read [Known limits](#known-limits) before relying on it for important work.

## Why use it

- Keep normal `claude` and `codex` unchanged.
- Start with an OpenAI or Anthropic model, and reach both from the same session.
- Use real Claude Code Agents for both providers, each with an exact model, and move them all with `/effort`.
- Keep native Agent cards, tools, background work, cancellation, and worktrees.
- Let Explore, Plan, and general-purpose inherit the main model or use an allowed exact model for one call.
- Set a budget mode and a smaller worker cap when you want one.
- See OpenAI plan usage without making a model request.
- Keep ignored files, env values, and known credentials out of isolated worker copies.

## How it works

```text
Plain airlock

Claude Code
    |
    `-- 127.0.0.1 OpenAI proxy
            `-- OpenAI subscription models

airlock hybrid

Claude Code
    |
    `-- 127.0.0.1 session router
          |-- exact gpt-* model ID --> local OpenAI proxy
          `-- exact claude-* ID   --> Anthropic
```

Plain `airlock` is OpenAI-only and connects directly to the local OpenAI proxy.

`airlock hybrid` starts one temporary router on `127.0.0.1`. The router sends exact enabled Claude model IDs to Anthropic and exact enabled GPT model IDs to the local OpenAI proxy. Claude Code removes the `[1m]` context suffix before an OpenAI request, so the router registers that one deterministic wire form alongside each enabled full GPT ID. Claude Code still owns every Agent call and tool event.

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

Clone this repository and run:

```bash
./scripts/setup.sh
```

The setup asks which models and limits you want. It shows a summary before changing anything.

For the tested defaults without the wizard:

```bash
./scripts/install.sh --with-agent
```

### Windows

You need:

- [Claude Code](https://code.claude.com/docs/en/setup)
- [Git for Windows](https://git-scm.com/download/win), including Git Bash
- Python 3
- The Windows build of [`claude-code-proxy`](https://github.com/raine/claude-code-proxy)
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

[Read the Windows guide](docs/windows.md).

## First run

OpenAI only:

```bash
airlock
airlock terra
airlock luna
```

Choose a main model while keeping both providers available:

```bash
airlock hybrid
airlock hybrid sol
airlock hybrid opus
airlock hybrid sonnet
```

Inside the session, ask for work normally. The main model can use:

- built-in Explore for read-only repository discovery
- built-in Plan for read-only technical design
- built-in general-purpose for multi-step work
- exact named workers such as `airlock-luna`, `airlock-sol`, `airlock-opus`, and `airlock-sonnet`

Built-in agents inherit the orchestrator model by default. The Agent call may give Explore, Plan, or general-purpose one exact enabled model ID. Plain `airlock` accepts enabled OpenAI IDs only. Hybrid accepts enabled OpenAI and Anthropic IDs. Aliases, disabled models, unknown IDs, and blocked extra-usage routes fail closed.

Named `airlock-*` workers have a fixed exact model and effort. A caller cannot change either one. They use Claude Code's normal subagent tools.

## How work is routed

The main model starts with the smallest useful approach:

1. Work directly for small, connected, or already understood work.
2. Use Explore for bounded read-only discovery.
3. Use Plan after the relevant code is known.
4. Use general-purpose for multi-step work that should stay with one model.
5. Use one exact named worker when another model is a better fit.
6. Use several Luna Agents only for independent high-volume work.
7. Keep integration and final synthesis with a stronger main model, Sol, or Opus.

Automatic armies are Luna-only. Each Luna or eligible Luna Fast shard runs at the session effort unless you pin one. Implementation shards need explicit file ownership, no-touch boundaries, and acceptance checks. Sol, Terra, Opus, Sonnet, Fable, and Haiku are never multiplied automatically.

For UI and UX work in a hybrid session, an exact user choice wins. Otherwise visual direction, product flows, new design systems, broad redesigns, and final visual critique prefer Opus. Sonnet fits bounded components and work that follows an existing design system.

[Read the detailed routing guide](docs/how-it-works.md).

## Common commands

```bash
airlock                         # OpenAI main model
airlock hybrid                  # choose an OpenAI or Anthropic main model
airlock terra                   # start with GPT-5.6 Terra
airlock luna                    # start with GPT-5.6 Luna
airlock mode                    # show routing and worker limits
airlock mode budget             # prefer lower use and block extra usage
airlock mode max-agents off     # use Claude Code's native worker limit
airlock mode max-agents 3       # save a smaller worker cap
airlock mode swarm-fast auto    # gate Luna Fast by plan and proxy support
airlock usage                   # refresh stale OpenAI usage and show it
airlock bundle                  # verify managed files
airlock models                  # list model shortcuts
```

Use native Claude Code's `/usage` screen for Anthropic subscription bars. Anthropic does not document a personal subscription API that Airlock can safely read.

[Read the models and usage guide](docs/models-and-usage.md).

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
- GPT model IDs may not appear in Claude Code's `/model` discovery list. Start the exact root with `airlock` or `airlock hybrid`, and use exact Agent model IDs through the guarded Agent call.
- Remote Control is unavailable when Claude Code uses a non-Anthropic base URL.
- Native Claude Code decides which tools subagents can use. Airlock cannot add a tool that Claude Code itself excludes from subagents.
- File hooks and prompts do not replace an operating-system sandbox.
- Subscription access, provider terms, model availability, and usage limits can change.

## Documentation

- [Getting started](docs/getting-started.md)
- [How it works](docs/how-it-works.md)
- [Models, limits, and usage](docs/models-and-usage.md)
- [Windows](docs/windows.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Testing](docs/testing.md)
- [Live tests that use plan quota](docs/live-tests.md)
- [Threat model](docs/threat-model.md)
- [Contributing](CONTRIBUTING.md)
- [Release process](docs/releasing.md)
- [Changelog](CHANGELOG.md)

## What this project does not do

- It does not modify native Claude Code or native Codex.
- It does not read, copy, or decode login token files.
- It does not make subscription limits unlimited.
- It does not silently switch an exact model to another provider.

## Credits

The OpenAI connection is powered by [`raine/claude-code-proxy`](https://github.com/raine/claude-code-proxy). That project handles OpenAI OAuth, request translation, streaming, and tool calls.

Airlock grew from the MIT-licensed Claudex project by Miguel Torrez. The original license and credit are preserved.

See [CREDITS.md](CREDITS.md) for the full list.

## License

This repository is released under the [MIT License](LICENSE). `claude-code-proxy` is a separate project with its own maintainers and license.

Claude Code is a product of Anthropic. Codex, GPT, ChatGPT, and OpenAI are products and marks of OpenAI. Airlock is an independent community project and is not affiliated with either company.
