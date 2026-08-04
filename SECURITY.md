# Security policy

Airlock handles local project files and routes model requests. Treat security problems seriously and report them privately.

## Report a security problem

Use the repository's **Security** tab and choose **Report a vulnerability** to open a private GitHub Security Advisory.

If private reporting is unavailable, do not open a public issue with working exploit details. Open a short issue asking the maintainers to enable private reporting without including sensitive details.

Include:

- the affected version from `VERSION`
- operating system and shell
- the command or feature involved
- safe steps using fake credentials and a small test repository
- the expected and actual result
- whether a provider request was sent

Do not include:

- OAuth access or refresh tokens
- credential file paths or contents
- account IDs or email addresses
- raw env values
- private prompts
- customer or private repository content
- full logs that may contain any of the above

## Keep both local services local

The OpenAI proxy uses:

```text
http://127.0.0.1:18765
```

Each hybrid session also starts a router on a temporary `127.0.0.1` port.

Do not expose either service to a LAN, VPN, container bridge, tunnel, or the public internet. The OpenAI proxy does not require an incoming client password.

The hybrid router accepts only exact enabled model IDs, the deterministic wire form Claude Code creates by removing `[1m]` from enabled GPT IDs, and a small set of Claude Code API paths. It rejects redirects and exits when its owning launcher exits.

## Protect provider credentials

Use only the proxy's official login commands:

```bash
claude-code-proxy codex auth login
claude-code-proxy codex auth device
```

Never copy a token into this repository, a shell alias, an env file, an issue, or a support message.

`ANTHROPIC_AUTH_TOKEN=unused` is a local placeholder used only by plain OpenAI mode. Hybrid mode clears that placeholder and requires the saved Claude subscription login path. Hybrid startup rejects a non-empty explicit Anthropic API key or a non-placeholder auth token instead of overwriting it.

Airlock never reads or decodes Codex or Claude credential files.

## Router credential boundary

On Anthropic routes, the router forwards Claude Code authorization and capability headers opaquely to `https://api.anthropic.com`.

On OpenAI routes, it removes:

- authorization
- API keys
- cookies
- proxy authorization
- Claude OAuth capability headers

It then calls only the loopback OpenAI proxy.

The router does not log or persist request bodies, response bodies, prompts, authorization headers, or credentials. Its loopback diagnostics endpoint keeps at most 256 in-memory events containing only the router instance ID, provider, exact enabled model, status, request and response byte counts, duration, sanitized outcome, and integer token counts read from the response that was already forwarded. The router reads only the numeric fields of a usage object and never keeps prompt or response text.

## Native repository Agents

Named `airlock-*` workers are real Claude Code Agents with exact fixed model IDs and efforts. They use Claude Code's native tool, permission, background, cancellation, usage, and worktree behavior.

Normal sessions allow:

- exact enabled named `airlock-*` Agents
- exact built-in Explore, Plan, and general-purpose Agents

Built-in Agents inherit the main model when no model field is supplied. They may receive one exact full model ID only when that ID is enabled for the active profile. Plain `airlock` accepts only enabled OpenAI IDs. Hybrid accepts enabled OpenAI and Anthropic IDs.

The guard rejects unknown Agent names, aliases, malformed or disabled model IDs, cross-profile IDs, blocked extra-usage routes, ineligible Fast routes, and model overrides on named Agents.

Named Agents disallow Agent and the session spawn depth is one. Fan-out stays at the root.

## Native worktree snapshots

A session-scoped WorktreeCreate hook builds an isolated snapshot from:

- current tracked files and tracked changes
- eligible non-ignored untracked regular files
- key-only env projections

The snapshot filters:

- known credential and key paths
- JSON files containing known credential fields
- complete private-key blocks
- ignored files
- links and Windows reparse points that are unsafe
- special files and outside-repository paths
- tracked files above 64 MiB
- tracked input above 512 MiB total
- untracked files above 16 MiB
- untracked input above 2,000 files or 64 MiB total
- files that change while the snapshot is prepared

A source file may contain private-key header text for a scanner or test. It is blocked only when it contains a complete key block with a valid body and matching footer.

The snapshot process does not stage, reset, clean, commit to, or change the user's branch, Git index, staging area, or working files. The isolated branch begins at a filtered synthetic commit.

The custom WorktreeRemove hook removes only an unchanged managed worktree. It preserves a worktree with uncommitted edits or additional commits.

Ignored files are never copied. An ignored env file is not projected because it is not eligible input.

## Direct sensitive-file guards

The session plugin runs before Read, Grep, Glob, and Bash.

An exact env read returns key names only. It does not expose values, prefixes, hashes, lengths, comments, defaults, or value-presence flags.

The guard blocks:

- known credential paths
- credential-bearing JSON
- complete private-key blocks
- broad searches that could return sensitive files
- obvious Bash references to sensitive paths, including Git object reads that name them

`.env.example`, `.env.sample`, and `.env.template` remain readable because they should contain safe examples.

These hooks are not a complete operating-system sandbox. Bash can execute repository code, and a tracked secret may already exist in shared Git history. Do not commit real secrets. Use WSL2 or stronger isolation for untrusted code on Windows.

## Public web research

Public web authorization is separate from repository access. A public web request must name the provider, exact model, public web state, extra-usage state, and worker count before a live test runs.

Public web prompts may contain only public questions, names, and URLs. They must not contain repository content, local paths, credentials, untracked data, or private prompts.

## Provider failures and billing

An exact provider or model never silently changes. Unknown router models fail locally.

Provider billing and spending settings are final. If paid credits or extra usage are enabled on a provider account, a local preference cannot guarantee zero paid usage.

`airlock usage` stores only sanitized OpenAI plan observations such as percentages, window lengths, reset times, safe plan labels, and credit availability returned by the documented Codex app server. These values are not a bill.

Use native Claude Code's `/usage` screen for Anthropic subscription bars. Airlock does not read Claude login files or scrape that screen.

## What installation changes

The installer changes only recognized Airlock managed files and the optional managed worker. It refuses unknown files and links. It writes the managed bundle marker after all protected launchers, helpers, catalogs, and plugin files are copied. Startup rejects a stale, changed, missing, or incomplete bundle.

The session plugin is passed only to `airlock`. It is not registered globally.

The installer does not change native Claude, native Codex, global settings, global hooks, registered plugins, MCP configuration, or unrelated agents.

Already running sessions keep their original permissions. Restart after a security update.

## Limits that remain

- Anthropic does not officially support non-Claude models behind a Claude Code gateway.
- GPT model IDs may not appear in Claude Code's gateway model discovery.
- Remote Control is unavailable behind a non-Anthropic base URL.
- A compromised local account, Claude Code, proxy, Git, Python, or operating system is outside this protection.
- Prompt rules and tool hooks reduce mistakes but do not make model behavior mathematically certain.
- Native Claude Code controls which tools subagents can use.
- Provider terms, billing rules, model access, and usage limits can change.

## More detail

Read the [threat model](docs/threat-model.md) for trusted parts, attack cases, and remaining limits.

Problems in OpenAI OAuth, protocol translation, or the OpenAI proxy should also be checked against [`raine/claude-code-proxy`](https://github.com/raine/claude-code-proxy) and reported under its security policy when they reproduce there.
