# Threat model

This document explains what Airlock tries to protect and where that protection ends.

## Shared local proxy, separate logins

Codex and Grok both reach their providers through the same loopback proxy, and the proxy picks the upstream from the model ID using its own stored login for that provider. Airlock never reads, copies, or forwards either login. The router strips Claude authorization, API-key, cookie, proxy authorization, and OAuth capability headers before any request reaches the loopback proxy, so a Claude credential cannot cross onto a Codex or Grok route.

Sharing one proxy process does mean Codex and Grok share its trust boundary. A compromised or malicious proxy build would see both. That risk already existed for Codex and is unchanged by adding Grok, but it is the reason Airlock keeps the proxy on loopback and pins the managed bundle it launches.

## What matters

The main things to protect are:

- provider login authorization
- private repository files
- raw env values
- the user's current Git branch, index, and working files
- control over provider, model, effort, extra usage, and parallel workers
- native Claude Code and Codex settings

## What Airlock trusts

Airlock trusts:

- the local user who starts `airlock`
- the installed Claude Code program
- the installed `claude-code-proxy` program
- Git, Bash, PowerShell, and Python on the local machine
- the operating system account and file permissions
- tracked repository content chosen by the user
- Anthropic and OpenAI endpoints selected by the local policy

Review `claude-code-proxy` separately. It owns the OpenAI OAuth flow and protocol conversion.

## What Airlock does not trust automatically

- model output
- arbitrary model-selected routes
- unknown Agent names or model aliases
- ignored or untracked files just because they are inside the repository
- links and Windows reparse points
- provider error text as proof of billing or quota
- a repository script simply because an Agent wants to run it
- a changed worktree simply because an Agent says it is safe to delete

## Main boundaries

### OpenAI-only profile

`airlock openai` and explicit OpenAI aliases point Claude Code directly at the OpenAI proxy on `127.0.0.1:18765`. Bare `airlock` also uses this path when setup saved the OpenAI-only profile. Only enabled OpenAI model IDs and Agents are allowed.

The proxy has no incoming client password. Do not expose it to a LAN, VPN, container bridge, tunnel, or public address.

### Hybrid router

A hybrid session starts one temporary router on `127.0.0.1`.

The router maps exact enabled model IDs to one provider. It does not use model prefixes alone as permission and it does not silently fail over.

For Anthropic, it forwards Claude Code authorization and capability headers opaquely to `https://api.anthropic.com`.

For OpenAI, it strips authorization, API-key, cookie, proxy authorization, and Claude OAuth capability headers before calling the loopback proxy.

The router preserves streamed bytes, rejects redirects, and exits when its owner exits. It does not log prompts, bodies, responses, or credentials. Its loopback diagnostics endpoint contains only a bounded in-memory list of sanitized route metadata, byte counts, and integer token counts.

A malicious local process running as the same user can still connect to a loopback service. The operating system account remains a trusted boundary.

### Native Agent calls

The session guard allows:

- exact enabled named `airlock-*` Agents
- exact built-in Explore, Plan, and general-purpose Agents

Built-ins inherit the orchestrator model unless one exact enabled full model ID is supplied for that call.

The guard blocks unknown names, aliases, malformed or disabled model IDs, cross-profile routes, ineligible Fast models, blocked extra-usage routes, and caller model overrides on named Agents.

Named Agents disallow Agent and spawn depth is one. Fan-out stays with the main model.

### Native worktrees

A WorktreeCreate hook builds an isolated synthetic commit from the current checkout. It uses current tracked content plus eligible non-ignored untracked regular files.

Before inclusion, it checks:

- normalized repository-contained paths
- regular file type and stable identity
- links and Windows reparse points
- known credential names
- credential-bearing JSON
- complete private-key blocks
- env projection rules
- per-file, file-count, and total-size limits

The hook uses a temporary Git index and staging directory. It does not change the user's branch, index, staging area, or working files.

The synthetic commit has no parent. This prevents the Agent's current branch history from directly inheriting a filtered parent commit. The worktree still shares the repository's Git object store and refs, as every Git worktree does.

The WorktreeRemove hook deletes only an unchanged managed worktree with the expected one-commit snapshot. It preserves edits and additional commits.

### Direct file tools

The session plugin runs before Read, Grep, Glob, and Bash.

It projects exact env reads to key names and blocks known credential paths, high-confidence credential content, broad sensitive searches, and obvious Bash path references.

This catches normal and accidental access. It is not a complete shell parser or operating-system sandbox. Bash can execute indirect commands and repository programs.

### Managed bundle integrity

The installer writes the bundle marker last. The launcher verifies hashes for launchers, helpers, Agent catalogs, and the session plugin.

A stale, changed, missing, or partially installed component stops startup. This catches accidental version mixing. It does not protect against a local attacker who can replace both the files and the trusted marker.

## Important attack cases

### Claude authorization reaches OpenAI

The router removes incoming authorization and OAuth capability headers on every OpenAI route. Protocol tests use synthetic headers and local fake upstreams to prove separation.

The router never logs a real authorization header, so live verification must use sanitized evidence only.

### A model requests an unknown model

The router and Agent guard both use active exact allowlists. An unknown, disabled, cross-profile, or ineligible model fails before provider routing.

### A model asks for credentials

The worktree snapshot filters known credentials and projects eligible env files. Direct tool hooks block normal raw reads and broad searches.

A tracked secret in Git history remains a separate risk. Do not commit secrets.

### A model lies about changed files

Claude Code and Git own the worktree state. The cleanup hook checks the real status and commit count. It does not trust the Agent's prose when deciding whether deletion is safe.

### A model starts too many Agents

Claude Code's native concurrency applies by default. The user can save a smaller cap. The ceiling is never a fan-out target.

Named Agents cannot invoke Agent, and the session spawn depth is one. Automatic armies are Luna-only and final synthesis stays with a stronger model.

### A malicious repository includes unsafe untracked files

The snapshot rejects ignored files, links, reparse points, special files, outside paths, credential-bearing files, oversized content, and files that change during preparation.

A repository can still contain harmful tracked code. Use WSL2 or another operating-system sandbox before running untrusted code.

### A provider fails or reaches quota

An explicit provider or model never silently changes. Claude Code may apply its normal retry behavior to an upstream response, but Airlock does not route an unknown model to a fallback provider.

Provider billing settings are final. Usage observations are not a bill.

## Limits that remain

- Anthropic does not officially support non-Claude models behind a Claude Code gateway.
- GPT IDs may not appear in Claude Code's gateway model discovery.
- Remote Control is unavailable behind a non-Anthropic base URL.
- A compromised local user account can read the same files as the user.
- A compromised Claude Code, proxy, Git, Python, shell, or operating system is outside this protection.
- A tracked secret may already exist in Git history and shared refs.
- Native Windows does not provide the same Bash sandbox as macOS, Linux, or WSL2.
- Native Claude Code controls which tools are available to subagents.
- Prompt rules and hooks reduce mistakes but cannot make model behavior mathematically certain.
- Provider terms, billing rules, model access, and usage limits can change.

## Reporting a problem

Follow [SECURITY.md](../SECURITY.md). Use fake credentials and a small test repository. Do not include a real token, credential path, private prompt, customer repository, or raw env value.
