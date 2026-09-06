# Threat model

This document explains what Airlock tries to protect and where that protection ends.

## Shared local proxy, separate logins

Codex and Grok both reach their providers through the same loopback proxy, and the proxy picks the upstream from the model ID using its own stored login for that provider. Airlock never reads, copies, or forwards either login. The router strips Claude authorization, API-key, cookie, proxy authorization, and OAuth capability headers before any request reaches the loopback proxy, so a Claude credential cannot cross onto a Codex or Grok route.

Sharing one proxy process does mean Codex and Grok share its trust boundary. A compromised or malicious proxy build would see both. That risk already existed for Codex and is unchanged by adding Grok, but it is the reason Airlock keeps the proxy on loopback and pins the managed bundle it launches.

## Session-local OpenAI Fast handoff

`airlock fast -r` starts fixed `gpt-5.6-sol-fast` for one session through the Codex proxy. It does not modify saved `AIRLOCK_OPENAI_FAST`, requires eligible OpenAI plan and proxy checks, and has no fallback. `/airlock-fast` arms the current managed session; only a clean exit lets the owning launcher resume the exact conversation once. It is not Claude Code Anthropic `/fast`.

The private handoff marker is nonce-, PID-, cwd-, and session-bound. It carries no credentials, prompts, transcript data, arbitrary executable data, or model choice, and the flow does not parse transcripts. Hard kill, crash, non-clean exit, hook failure, or expiry prevents relaunch. The installed managed plugin SessionEnd hook is required and is authorized as part of the plugin, not global hooks.

## What matters

The main things to protect are:

- provider login authorization
- private open-model endpoint and upstream model identities
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
- an explicitly declared loopback inference server as a processor for the prompts sent to it

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
- the real capability, context window, cost, or behavior of a user-declared OpenRouter model
- the real capability, context window, output limit, tool behavior, or quality of a user-declared open model

## Main boundaries

### OpenAI-only profile

`airlock openai` and explicit OpenAI aliases point Claude Code directly at the OpenAI proxy on `127.0.0.1:18765`. Bare `airlock` also uses this path when setup saved the OpenAI-only profile. Only enabled OpenAI model IDs and Agents are allowed.

The proxy has no incoming client password. Do not expose it to a LAN, VPN, container bridge, tunnel, or public address.

### Session router

A hybrid session, an `airlock opr` session, and every open-model session each start one temporary router on `127.0.0.1`. The same router implementation and the same protections below apply to all of them; the [OpenRouter routes](#openrouter-routes) and [open-model routes](#open-model-routes) sections cover their provider-specific behavior.

The router maps exact enabled model IDs to one provider. It does not use model prefixes alone as permission and it does not silently fail over.

For Anthropic, it forwards Claude Code authorization and capability headers opaquely to `https://api.anthropic.com`.

For OpenAI, it strips authorization, API-key, cookie, proxy authorization, and Claude OAuth capability headers before calling the loopback proxy.

Existing subscription routes preserve streamed bytes. Open-model routes instead translate bounded Chat Completions events into Anthropic events. Every route rejects redirects, and the router exits when its owner exits. It does not log prompts, bodies, responses, private local identities, or credentials. Its loopback diagnostics endpoint contains only a bounded in-memory list of sanitized route metadata, byte counts, and integer token counts.

A malicious local process running as the same user can still connect to a loopback service. The operating system account remains a trusted boundary.

### Windows launch artifacts

The Windows process API limits the full command line. Airlock stores generated session settings and combined routing guidance in private per-user runtime files while Claude Code runs, passes only their paths to Claude Code, and removes them after exit or startup failure. The guidance file can contain private text supplied through `--append-system-prompt`, so Airlock checks the runtime directory, creates the file privately, and verifies its path, identity, size, and digest before deletion. A changed file is not deleted. Agent JSON remains inline because Claude Code provides no file-based Agent option.

These files never contain provider credentials. Another process already running as the same operating-system user can still read user-owned session files, so the local account remains the trusted boundary. Airlock checks the exact remaining UTF-16 command length before it creates the session snapshot or starts the router. The refusal reports only the length, not the command or prompt.

### OpenRouter routes

OpenRouter is optional and off by default. Nothing about it is trusted the way Anthropic and OpenAI are: every route is a model and endpoint that you declared, not one Airlock chose or verified for quality.

`airlock openrouter models add ROUTE MODEL ENDPOINT` checks that both public catalog responses identify the exact routable model, rejects a declared non-null alias target and every dynamic selector identity, verifies that the exact endpoint serves it, requires both to report `tools` and `tool_choice` support, and asks for confirmation by name before saving. It freezes the endpoint tag, catalog provider name, provider-registry routing slug, endpoint quantization, and separate canonical slug. The provider name must map to one slug, and provider plus quantization must identify one endpoint. If any frozen identity changes on refresh, Airlock refuses the update rather than silently accepting the remap. A route's verified metadata expires after 30 days; an expired or otherwise invalid registry entry makes the whole registry invalid, which stops every Airlock session, not only ones using OpenRouter, until it is refreshed or removed.

Curated presets do not bypass these checks. A preset freezes one expected model, canonical slug, route, and endpoint, then uses the same confirmed add path. Its project-controlled community guidance appears only while the declared model, endpoint, and canonical provenance all match that frozen preset. The guidance is used only in a generated Agent description. It is not accepted from registry data or command arguments and does not enter the registry, snapshot, route policy, or Agent prompt. Setup and installation do not add presets automatically.

The router constrains each OpenRouter request to the verified provider-registry slug and endpoint quantization, with OpenRouter's own fallback routing turned off. The catalog check rejects that pair if it identifies more than one current endpoint and requires `tools` and `tool_choice` support. Airlock does not require the endpoint to advertise every optional field in Claude Code's native Messages payload, because doing so can exclude the exact pinned endpoint even when its required tool fields are supported. Claude Code may encode its custom-model notice as a non-standard `messages` entry with the `system` role. Airlock preserves text-only notice content by lifting it into the documented top-level `system` field; ambiguous variants fail locally. A response is forwarded only when its model is the exact routable ID or the frozen canonical slug. The router adds only the locally stored OpenRouter key and strips the same incoming headers it strips for OpenAI. It does not support `count_tokens` for OpenRouter routes.

A declared route becomes a named `airlock-or-ROUTE` Agent inside a normal hybrid Airlock session, and the generated Agent allow-list exposes it only by that name. This is a launcher and session-policy guarantee, not authentication on the loopback router. A different process already running as the same user could call an exact route on that temporary loopback service while the session is alive. The operating system account remains the trusted boundary.

`airlock opr ROUTE` is the one exception to "never a root": it starts an OpenRouter-only session whose exclusive root is exactly the declared route you name, or the one you pick from an offline interactive list of your declared routes. It fails closed on an unknown, disabled, or misspelled route rather than falling back to another one, and it needs no Claude, Codex, or Grok credential and starts no OpenAI subscription proxy. It also has no saved default: every launch names a route explicitly or asks, and running it does not change guided setup or any saved profile. Because that root route is the one you explicitly chose for the session, it is not itself gated by `AIRLOCK_EXTRA_USAGE_POLICY`, the same way an explicit hybrid root such as `airlock hybrid opus` is not treated as extra usage. Any other declared route that becomes visible in that same `opr` session, or any route inside a hybrid session, is a separate `airlock-or-ROUTE` Agent and follows the normal extra-usage policy: `airlock openrouter auth` and `airlock openrouter models` do not start a session or a root by themselves, and there is no OpenRouter OAuth. Airlock does not verify a model's real capability, context window, or cost, whether it runs as an `opr` root or as an extra-usage Agent.

The OpenRouter key is stored only through the operating system's own credential protection (the macOS Keychain, Windows DPAPI scoped to the current Windows user, or the Linux Secret Service), with no plaintext file fallback. A compromised local account can still read it, the same boundary that already applies to Codex and Claude credentials.

### Open-model routes

Open-model support is optional and off by default. Each endpoint and route comes from a protected local `openmodel-registry.json` written by explicit management commands. Endpoint URLs must be canonical `http://127.0.0.1:PORT/v1` values. Airlock rejects names, alternate loopback addresses, IPv6, non-loopback addresses, other schemes or paths, encoded variants, user information, queries, and fragments.

The private URL and upstream identity do not appear in management argv, the public `openmodel/ROUTE` model ID, or the `airlock-om-ROUTE` Agent name. Add commands read private values from bounded hidden prompts or bounded exact-schema JSON on stdin. The values stay in the protected registry and protected signed session snapshot. Registry updates use regular-file and link checks, current-user file protection, bounded duplicate-free JSON, a private lock, atomic replacement, and digest comparison. The router uses only its signed snapshot, so changing the registry cannot redirect a running session.

The adapter builds a fresh allowlisted Chat Completions body. It strips incoming authorization, keys, cookies, proxy authorization, provider capability headers, and arbitrary headers. Direct `HTTPConnection` transport does not use inherited proxy configuration and follows no redirect. Before a successful response is committed, the server must report one exact configured private identity. The router never sends the request to a second model after a local failure.

The declared context, output, streaming, tool-count, and tool-choice capabilities are not proof of server behavior. Airlock enforces the declaration and rejects unsupported client semantics or a response that exceeds its declared tool behavior. Histories that Chat Completions cannot represent faithfully, including failed, missing, duplicate, late, or interrupted tool results and assistant text after a tool call, are rejected before network access rather than reordered or weakened. Every pending tool call must receive one immediate result before user text or a later message. `airlock open-model check ROUTE` verifies only one bounded credential-free catalog identity. It does not prove generation or capabilities. Doctor remains offline.

Every endpoint has one concurrency limit shared across its route aliases. The semaphore is held for the complete request or stream and released on success, errors, and disconnects. This protects a single-slot local server from Airlock concurrency, but another process running as the same user can still contact that server directly.

A pure `airlock om ROUTE` session needs no provider login or subscription proxy. `airlock hybrid om:ROUTE` keeps ordinary non-local workers. Neither path puts a local route into saved `auto`, discovery, automatic swarms, handoff, provider fallback, capacity routing, or synthesized compactor routing. Airlock does not manage the inference process, model files, hardware, or server flags.

### The console

`airlock console` binds to `127.0.0.1` only and refuses to start on a port already held by something that is not an Airlock console. It aggregates state across sessions by reading registry files the routers already write and by polling each router's own `/diagnostics`; it never gains a capability a router did not already expose. One live console is allowed per runtime root. Its private, process-lifetime `console.lock` prevents another same-root console from serving, while different roots remain independent. A second start exits safely and, when the marker validates, the launcher opens the existing console. Operating-system lock release permits recovery after a hard crash; a stale non-secret marker may remain, and the next start that obtains the lock replaces it.

Every registry file carries a private per-router control token, at least 256 random bits, stored only in that mode-0600 file. The token is the only thing that lets the console pin or unpin that one router's session; the router accepts nothing else through it, no chain edit, no process operation, no generic action. The console's separate address marker holds only a schema version, loopback URL, process ID, and random console identity. It never carries a router control token or CSRF token. A separate per-process CSRF token, injected only into the served root page, protects browser-originated mutations from cross-site requests. Neither token is ever returned by a JSON endpoint, logged, or rendered anywhere in the page, an event, a report, an MCP response, or WebMCP.

Agent-facing channels, the `airlock-console-tools` MCP server, the console's own HTTP tool endpoints, and WebMCP inside the page, can read state and create proposals. None of them can approve, reject, execute, pin, unpin, resume, or otherwise reach a router-control operation. Approving a proposal, or pinning a session directly, is a person clicking a button in the browser with a valid CSRF token; the effect lands immediately on the live session's router without restarting anything.

The console renders only an allowlisted set of fields from routers and registry files, treats both as untrusted input, validates their schema, and caps their size. It never renders prompt or response text, provider error bodies, tokens, or account identifiers, and it never restarts, signals, or otherwise writes to a router or a session outside of an approved proposal or a direct pin. Its static site is installed through a same-parent staging directory and one recoverable managed backup container. Unsafe, ambiguous, writable, reparse-point or symlinked, and malformed backups fail closed. POSIX ownership checks refuse a backup owned by another user. Windows relies on the user-owned install root and reparse refusal; it does not apply POSIX ownership checks. The managed bundle marker is written last.

A malicious local process running as the same user can still reach the console's loopback API, read a registry file, or automate the browser exactly the way it could reach any other loopback service or local file today. The operating system account remains the trusted boundary; the console does not add one.

### Native Agent calls

The session guard allows:

- enabled named `airlock-*` Agents with exact model identities
- built-in Explore, Plan, and general-purpose Agent types with validated family slots

Plan and general-purpose inherit the orchestrator unless a schema-valid `fable`, `opus`, `sonnet`, or `haiku` alias is supplied. Routine Explore uses the `haiku` slot when it would otherwise spend a different premium root.

The guard blocks unknown names or model values, malformed or stale family maps, disabled exact targets, cross-profile routes, ineligible Fast models, blocked extra-usage routes, and caller model overrides on named Agents.

Named Agents disallow Agent at the default depth of 1, and fan-out stays with the main model. At depth 2 a named Agent may invoke Agent, but only to spawn its own Agent type, so every descendant runs the model the root chose for that worker. A caller still cannot override a worker's model, and the guard denies a mismatched type or a model override on a nested call.

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

### Cached update notice

Only an explicit `airlock update --check` contacts GitHub and writes update state. Normal startup reads one local file under the Airlock config root and makes no update network request.

The SessionStart reader accepts only a regular file of at most 4096 bytes with the exact schema, strict versions, canonical Airlock release URL, matching installed version, eligible release channel, and a check time within seven days. It rejects links, stale data, malformed JSON, duplicate fields, unknown fields, and unsafe output. A valid record becomes only Claude Code's top-level `systemMessage`; it is not returned as model context. Every invalid or internal-error path exits successfully without stdout or stderr so an untrusted cache cannot create a prompt or a hook error.

The cache is only a reminder. Installation still happens outside the active session through the verified updater and managed-file conflict checks. A local process with the user's file permissions can delete the reminder, but it cannot use the cache to bypass release verification or installation confirmation.

## Important attack cases

### Claude authorization reaches OpenAI

The router removes incoming authorization and OAuth capability headers on every OpenAI route. Protocol tests use synthetic headers and local fake upstreams to prove separation.

The router never logs a real authorization header, so live verification must use sanitized evidence only.

### A model requests an unknown model

The router and Agent guard both use active exact allowlists. An unknown, disabled, cross-profile, or ineligible model fails before provider routing.

### A local registry tries to redirect traffic

The registry validator accepts only literal canonical `127.0.0.1` endpoint URLs and protected regular files. The signed session snapshot freezes the endpoint for one router lifetime. DNS, redirects, links, changed files, private-network addresses, and public addresses cannot retarget an accepted route.

### A local server violates its declaration

The adapter checks the exact private response identity, declared streaming and tool behavior, response shape, JSON function arguments, finish reason, usage shape, and stream bounds. A failure before stream commitment returns a sanitized local error. A failure after commitment emits a sanitized in-stream error and stops. Neither case retries on another model or reflects the private identity, endpoint, response body, headers, or `reasoning_content`.

### A model asks for credentials

The worktree snapshot filters known credentials and projects eligible env files. Direct tool hooks block normal raw reads and broad searches.

A tracked secret in Git history remains a separate risk. Do not commit secrets.

### A model lies about changed files

Claude Code and Git own the worktree state. The cleanup hook checks the real status and commit count. It does not trust the Agent's prose when deciding whether deletion is safe.

### A model starts too many Agents

Claude Code's native concurrency applies by default. The user can save a smaller cap. The ceiling is never a fan-out target.

Named Agents cannot invoke Agent at the default depth of 1. Automatic armies are Luna-only and final synthesis stays with a stronger model. At depth 2 a worker's descendants are pinned to that worker's own model.

### A malicious repository includes unsafe untracked files

The snapshot rejects ignored files, links, reparse points, special files, outside paths, credential-bearing files, oversized content, and files that change during preparation.

A repository can still contain harmful tracked code. Use WSL2 or another operating-system sandbox before running untrusted code.

### A provider fails or reaches quota

An explicit provider or model never silently changes. Claude Code may apply its normal retry behavior to an upstream response, but Airlock does not route an unknown model to a fallback provider.

Provider billing settings are final. Usage observations are not a bill.

### A declared OpenRouter route drifts or expires

OpenRouter can change what an endpoint serves after you declared it. Airlock does not poll for that; it only re-checks when you run `airlock openrouter models refresh`, and a route's verified metadata expires after 30 days on its own. A refresh rejects a changed canonical slug, but an upstream remap that happens inside the 30-day window remains a residual risk until the next refresh. Exact endpoint pinning and disabled fallback prevent OpenRouter from selecting a different endpoint, but they cannot make an endpoint's own behavior immutable.

An expired or otherwise invalid registry entry does not quietly disable that one route. It makes the whole registry file invalid, which stops every Airlock session, including OpenAI-only and Grok-only ones, until the route is refreshed with `--apply` or removed. This is a deliberate fail-closed choice: a stale route is treated as untrusted input rather than left running on unverified metadata.

### An agent tries to approve its own proposal

The console's agent-facing surfaces, the MCP server, the HTTP tool endpoints, and WebMCP, only expose tools that read state or create a proposal. There is no approve, reject, pin, unpin, resume, or router-control tool defined on any of them, so an agent cannot complete the loop on its own even if it created the proposal. Only a person, acting in the browser with a valid same-origin request and CSRF token, can move a proposal to applied.

## Limits that remain

- Anthropic does not officially support non-Claude models behind a Claude Code gateway.
- GPT IDs may not appear in Claude Code's gateway model discovery.
- Remote Control is unavailable behind a non-Anthropic base URL.
- A compromised local user account can read the same files as the user, including the operating system's own credential storage that holds a stored OpenRouter key.
- A user-declared OpenRouter route is only checked against the public catalog for identity and tool support. Airlock does not evaluate its real capability, context window, or cost.
- A user-declared open model and its local server are trusted to process the prompts explicitly sent to them. The catalog check proves only an exact identity at one moment, not capability, quality, safety, uptime, context size, or correct inference.
- A compromised Claude Code, proxy, local inference server, Git, Python, shell, or operating system is outside this protection.
- A tracked secret may already exist in Git history and shared refs.
- Approving a proposal in the console is not a security boundary against software already running with the person's local account privileges. It separates normal agent tool access from the approval path, nothing more.
- Native Windows does not provide the same Bash sandbox as macOS, Linux, or WSL2.
- Native Claude Code controls which tools are available to subagents.
- Prompt rules and hooks reduce mistakes but cannot make model behavior mathematically certain.
- Provider terms, billing rules, model access, and usage limits can change.

## Reporting a problem

Follow [SECURITY.md](../SECURITY.md). Use fake credentials and a small test repository. Do not include a real token, credential path, private prompt, customer repository, or raw env value.
