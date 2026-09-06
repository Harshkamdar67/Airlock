# Security policy

Airlock handles local project files and routes model requests. Treat security problems seriously and report them privately.

## Supported versions

Before 1.0, security fixes are provided for the latest tagged beta only. Older betas may not receive patches. The default branch is development code and is not a supported release.

| Version | Supported |
| --- | --- |
| Latest tagged beta | Yes |
| Older betas | No |
| Unreleased default branch | No |

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

## Keep local services local

The OpenAI proxy uses:

```text
http://127.0.0.1:18765
```

Each hybrid or open-model session also starts a router on a temporary `127.0.0.1` port.

A user-hosted open-model endpoint must use exactly `http://127.0.0.1:PORT/v1`, with a literal IPv4 address and an explicit port. Airlock rejects `localhost`, DNS names, IPv6, other loopback aliases, LAN or VPN addresses, HTTPS, user information, redirects, queries, fragments, encoded paths, and any other path. Do not expose the inference server to a LAN, VPN, container bridge, tunnel, or the public internet.

Do not expose the Airlock router or OpenAI proxy either. The OpenAI proxy does not require an incoming client password.

The hybrid router accepts only exact enabled model IDs, the deterministic wire form Claude Code creates by removing `[1m]` from an enabled native Claude ID that carries it, and a small set of Claude Code API paths. Legacy suffixed GPT IDs are normalized before they can enter the router policy. It rejects redirects and exits when its owning launcher exits.

## Protect provider credentials

Use only the proxy's official login commands. After installation, Airlock's wrapper invokes those exact upstream commands with the configured private proxy directory:

```bash
airlock proxy auth login
airlock proxy auth device
```

During first installation, `./scripts/install.sh --login` invokes `claude-code-proxy codex auth login` directly with the same supported directory selection.

Never copy a token into this repository, a shell alias, an env file, an issue, or a support message.

`ANTHROPIC_AUTH_TOKEN=unused` is a local placeholder used only by plain OpenAI mode. Hybrid mode clears that placeholder and requires the saved Claude subscription login path. Hybrid startup rejects a non-empty explicit Anthropic API key or a non-placeholder auth token instead of overwriting it.

Airlock never reads or decodes Codex or Claude credential files.

## Session-local OpenAI Fast handoff

The direct shortcut `airlock fast -r` starts one session-root `gpt-5.6-sol-fast` through the local Codex proxy. It does not change saved `AIRLOCK_OPENAI_FAST`; eligible OpenAI plan and proxy checks still apply, and there is no fallback to another model.

Inside a managed Airlock session, `/airlock-fast` arms the current session. The user must exit cleanly. The owning launcher then resumes the exact conversation once on fixed `gpt-5.6-sol-fast`. This is not Claude Code Anthropic `/fast`. A hard kill, crash, non-clean exit, SessionEnd hook failure, or expired handoff intentionally prevents relaunch.

The handoff marker is private and bound to a nonce, launcher PID, working directory, and Claude session ID. It does not parse transcripts and carries no credentials, prompts, arbitrary executable data, or model choice. The installed managed plugin SessionEnd hook is required for the handoff and is authorized as part of Airlock's plugin, not through global hooks.

## Protect the OpenRouter credential

OpenRouter is optional and separate from Codex and Claude. It stays off until you turn it on:

```bash
airlock openrouter auth set-key
airlock openrouter auth status
airlock openrouter auth logout
```

The key is stored only through the operating system's own credential protection: the macOS Keychain, Windows DPAPI scoped to the current Windows user, or the Linux Secret Service through `secret-tool`. Airlock never writes the key to a plain file, an env file, or the Airlock config, and there is no plaintext fallback. If none of those backends is available on the machine, `set-key` fails instead of storing the key unprotected.

Airlock checks locally that the value has the shape of a real OpenRouter key before storing it (it starts with `sk-or-v1-`). It does not send the key to OpenRouter just to test it. There is no OAuth step for OpenRouter.

`airlock openrouter auth logout` asks for confirmation before it deletes the stored key, unless you pass `--yes`.

## The OpenRouter model registry

Every OpenRouter route is one you add yourself:

```bash
airlock openrouter models add ROUTE MODEL ENDPOINT
```

Nothing is declared by default, and the guided setup does not create a route. Adding or refreshing one fetches public model and endpoint metadata from `openrouter.ai` without sending your key, and asks for confirmation by name unless you pass `--yes`. Airlock requires the exact routable model ID in both catalog responses, rejects a declared non-null alias target and every dynamic selector identity, verifies the exact endpoint that serves it, and requires shared support for `tools` and `tool_choice`. It freezes the endpoint tag, catalog provider name, provider-registry routing slug, endpoint quantization, and separate canonical slug. The provider name must map to exactly one routing slug, and provider plus quantization must identify only one listed endpoint, or the route is rejected as ambiguous. If any frozen identity changes during refresh, Airlock stops rather than silently accepting the remap. The credential-free session snapshot carries this bounded routing metadata so the router can pin the request and accept only the exact routable or frozen canonical model identity in a response. None of it is added to the provider request as prompt content. `airlock openrouter models refresh` reports metadata changes without saving anything unless you also pass `--apply`.

A route's verified metadata expires after 30 days. An expired or otherwise invalid registry entry makes the whole registry file invalid, which stops every Airlock session, not only ones using OpenRouter, until you refresh the route with `airlock openrouter models refresh --apply` or remove it with `airlock openrouter models remove ROUTE`.

Registry updates use a private lock file and compare the validated registry digest with the version the command originally read. If another `add`, `remove`, or applied `refresh` wins first, the later command stops and asks you to rerun it instead of dropping or restoring someone else's change.

A declared route can appear a second way, as a named `airlock-or-ROUTE` Agent inside a hybrid session. Because Airlock does not verify a declared model's real capability, context window, or cost, that Agent is treated as extra usage and follows the same `AIRLOCK_EXTRA_USAGE_POLICY` as any other extra-usage worker: it needs `Extra usage authorized: yes` under the default `ask` policy, it runs without asking under `allow`, and it is not offered at all under `never`.

## The exclusive OpenRouter root: `airlock opr`

```bash
airlock opr                 # interactive picker over your declared routes
airlock opr ROUTE           # exact declared route as the session root
```

`airlock opr` starts an OpenRouter-only session whose root is exactly one route you already declared. Give the route name directly, or omit it in an interactive terminal to choose from the local registry; outside an interactive terminal `airlock opr` refuses to guess and asks for an exact route instead. An unknown, misspelled, or disabled route is rejected before any request is made, the same fail-closed check `airlock openrouter auth|models` themselves would use to look the route up.

The root you select this way carries normal session traffic. It is not itself subject to `AIRLOCK_EXTRA_USAGE_POLICY`, the same way an explicit `airlock hybrid opus` root is not treated as extra usage in a hybrid session. If your registry has other declared routes, they can still become additional named `airlock-or-ROUTE` Agents inside that same `opr` session, and those additional routes follow `AIRLOCK_EXTRA_USAGE_POLICY` exactly as they would in a hybrid session: confirmation under `ask`, no confirmation under `allow`, not offered under `never`.

An `opr` session has no dependency on Claude, Codex, or Grok. It does not read Claude or Codex OAuth state, does not require or start the OpenAI subscription proxy, and does not touch Grok login. Only the stored OpenRouter key and the declared route matter.

`airlock opr` never saves a default. Every launch names its route explicitly or asks interactively; there is no `airlock mode` or config setting that makes one OpenRouter route start automatically, and running `airlock opr` does not change guided setup or any saved profile.

## Where the OpenRouter key can and cannot appear

The stored OpenRouter key is read only by the small process that needs it and only from operating-system credential storage: the OpenRouter credential helper when you run `airlock openrouter auth`, and the session's loopback router when it forwards a request. Neither process is handed the key on its command line or through an inherited environment variable it did not read itself.

- **argv:** `airlock opr`, `airlock openrouter auth`, and `airlock openrouter models` never accept a key as a command-line argument. `set-key` reads it from a hidden interactive prompt or from bounded standard input.
- **Prompts sent to a model:** the key is never composed into system-prompt guidance, Agent descriptions, or any text sent to Claude Code or a provider.
- **Session snapshots:** the credential-free session snapshot that the router reads carries only routing metadata (route, model, endpoint tag, provider slug, quantization, canonical slug), never the key.
- **The registry file:** the local `openrouter-registry.json` in your Airlock config directory holds routing metadata only (route, model, endpoint tag, provider slug, quantization, canonical slug). The key lives in OS credential storage, never in the registry.
- **Diagnostics:** the router's loopback diagnostics endpoint reports only instance ID, provider, exact model, status, byte counts, duration, and integer token counts. It never includes the key or any request or response body.
- **The launched Claude Code process's environment:** before starting an `opr` (or hybrid) session, Airlock explicitly unsets `OPENROUTER_API_KEY`, along with `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`, `CODEX_API_KEY`, `XAI_API_KEY`, and `GROK_API_KEY`, so the child Claude Code process and anything it spawns never inherit the OpenRouter key or any other provider credential. The router process itself also starts with a minimal, explicitly allow-listed environment and loads the key itself from OS credential storage rather than receiving it from its parent.

## The private open-model registry

Open-model support is optional and explicit. Airlock creates no local route during installation or guided setup. You add an endpoint and route yourself with `airlock open-model`. The protected `openmodel-registry.json` file keeps the private endpoint URL and upstream model identity separate from the public `openmodel/ROUTE` wire ID and `airlock-om-ROUTE` Agent name. Add commands never accept those private values in argv: an interactive command uses a bounded hidden prompt, while `--stdin` accepts only a bounded, exact-schema UTF-8 JSON object.

Airlock accepts only regular, non-linked registry files protected for the current user. Registry updates use a private lock, canonical JSON, atomic replacement, and digest comparison. If another update wins first, the later command stops instead of overwriting it. The signed session snapshot freezes the exact endpoint and route metadata used by that router process, so a registry edit cannot redirect a running session.

`airlock open-model check ROUTE` makes one bounded, credential-free `GET /v1/models` request to that route's loopback endpoint and requires its exact private model identity. It follows no redirect and changes no registry state. Ordinary lists, errors, Doctor output, guidance, Agent definitions, diagnostics, and router errors do not print the endpoint URL or upstream identity. Doctor never contacts an inference server.

Capability declarations are the user's claim, not an Airlock benchmark. A route must declare its context window, output ceiling, streaming support, tool count, supported tool-choice modes, and whether to create a named worker. Airlock enforces those declarations but does not infer or verify them. Endpoint concurrency belongs to the endpoint, so every route alias that shares one server also shares its single bounded semaphore.

## Explicit open-model roots

```bash
airlock om ROUTE
airlock hybrid om:ROUTE
```

A pure open-model session needs no Claude, Codex, Grok, or OpenRouter login and does not start the subscription proxy. A hybrid open-model root keeps the ordinary non-local worker pool and starts only the provider services those routes need. Open models are never saved defaults and never join `auto`, discovery, automatic swarms, handoff, provider fallback, capacity routing, or synthesized compactor routing. A local route is used only when the user names its root or exact Agent.

Airlock connects to an already-running server. It never downloads a model, changes GPU settings, selects llama.cpp or another server's flags, starts the server, or stops it.

## Windows session launch files

Windows limits the complete command passed to `CreateProcessW`. Airlock therefore writes generated Claude Code settings and combined routing guidance to private files in its per-user session runtime directory, then passes only their paths through `--settings` and `--append-system-prompt-file`. This avoids putting the guidance text on the Windows command line. Exact Agent JSON remains inline because Claude Code has no Agent-file option.

A guidance file contains the same system-prompt text that Claude Code would otherwise receive directly, including any text the user supplied through `--append-system-prompt`. It can therefore contain private prompt text. Airlock creates each file with the same private runtime-directory pattern used for session policy snapshots, flushes it before launch, and records its exact size and digest. After Claude Code exits or startup fails, Airlock verifies the path, file identity, size, and digest before removing it. A changed or replaced file is left in place with a warning instead of deleting an unverified target. The operating system account remains the trusted boundary while the session is running.

No provider credential is written to these files. Airlock measures the remaining Windows command before it creates the policy snapshot or starts the router, and reports only the measured length if it is still too large.

## Router credential boundary

On Anthropic routes, the router forwards Claude Code authorization and capability headers opaquely to `https://api.anthropic.com`.

On OpenAI routes, it removes:

- authorization
- API keys
- cookies
- proxy authorization
- Claude OAuth capability headers

It then calls only the loopback OpenAI proxy.

On OpenRouter routes, it strips the same incoming headers, adds only the locally stored OpenRouter key, and constrains OpenRouter routing to the verified provider-registry slug and endpoint quantization with fallback routing turned off. Catalog verification still requires `tools` and `tool_choice`, but the request does not require the endpoint to advertise every optional field in Claude Code's native Messages payload. Claude Code's custom-model notice can arrive as a non-standard `messages` entry with the `system` role; Airlock preserves its text and moves it into the Anthropic Messages API's top-level `system` field before forwarding. Any such entry with extra message fields, non-text content, or no remaining user or assistant message is rejected locally rather than transformed ambiguously. Airlock accepts a successful response only when its model is the exact routable ID or the frozen canonical slug. It does not support OpenRouter's `count_tokens` operation; it refuses that request instead of inventing a token estimate for a model it has not verified.

On open-model routes, the router creates a fresh OpenAI Chat Completions request from an allowlist. It never forwards the original Anthropic body, incoming authorization, cookies, API keys, proxy authorization, arbitrary configured headers, or inherited proxy settings. It connects directly to the signed snapshot's literal `127.0.0.1` port, follows no redirect, and accepts only an exact declared private response identity before committing a successful response. Errors can name only the public `openmodel/ROUTE` identity. Open-model traffic is never retried on another model.

The adapter rejects unsupported content and undeclared tool behavior before network access. It converts text, client function calls, and successful tool results in both directions and preserves ordering that Chat Completions can represent. Every pending call must receive one immediate result before user text or a later message; failed, missing, duplicate, late, or interrupted results and assistant text after a tool call are rejected instead of changing their meaning. It bounds streamed state and discards `reasoning_content` without logging it. The local MVP does not support the Responses API, native Ollama API, images, documents, audio, embeddings, server-side tools, thinking or redacted-thinking blocks, token counting, or any path except `/v1/messages`.

The router does not log or persist request bodies, response bodies, prompts, authorization headers, or credentials. Its loopback diagnostics endpoint keeps at most 256 in-memory events containing only the router instance ID, provider, exact enabled model, status, request and response byte counts, duration, sanitized outcome, and integer token counts read from the response that was already forwarded. The router reads only the numeric fields of a usage object and never keeps prompt or response text.

## Native repository Agents

Named `airlock-*` workers are real Claude Code Agents with exact fixed model IDs. Their effort follows the session unless the config pins it. They use Claude Code's native tool, permission, background, cancellation, usage, and worktree behavior.

Normal sessions allow:

- enabled named `airlock-*` Agents with exact model identities
- built-in Explore, Plan, and general-purpose Agent types with policy-owned family slots

Built-in Plan and general-purpose inherit the main model when no model field is supplied. Explore also inherits when the root is already the economical discovery route. Otherwise the guard requires the schema-valid `haiku` family alias. A built-in call may use only Claude Code's `fable`, `opus`, `sonnet`, or `haiku` alias, and Airlock validates the exact enabled model behind that slot. Bare `airlock` starts whichever profile setup saved.

The guard rejects unknown Agent names or model values, malformed or stale family maps, disabled exact targets, cross-profile routes, blocked extra-usage routes, ineligible Fast routes, and model overrides on named Agents.

Named Agents disallow Agent at the default Agent depth of 1, and fan-out stays at the root. At depth 2 a named Agent may invoke Agent, but only to spawn its own Agent type, so every descendant runs the model the root chose for that worker. A caller still cannot override a worker's model, and the guard denies a mismatched type or a model override on a nested call.

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

## Cached update notices

Only `airlock update --check` contacts GitHub for an update notice. A later Airlock startup, resume, or clear reads the bounded local cache and performs no update network request.

The managed reader rejects links, non-regular or oversized files, malformed or unexpected JSON, invalid versions, the wrong release channel or repository URL, an installed-version mismatch, and data older than seven days. A valid notice uses Claude Code's user-only `systemMessage` field and is not added to model context. Invalid state exits silently and never blocks startup.

The notice does not install code or weaken updater verification. Exit the active session and run `airlock update` to download, verify, confirm, and install the release.

## The console

`airlock console` starts a local server that shows every Airlock session on the machine. It binds to `127.0.0.1` only and refuses to start if the port is already used by something that is not an Airlock console. One live console is allowed per runtime root. It holds a private, process-lifetime `console.lock` next to its address marker, so another start exits safely. A validated marker lets the launcher open the existing console. Operating-system lock release permits hard-crash recovery; a stale non-secret marker may remain, and the next start that obtains the lock replaces it. Different runtime roots are independent. While the console serves, on port 4783 or a port chosen with `--port`, it atomically writes a private, non-secret `<console runtime root>/console-address.json` marker with a schema version, loopback URL, process ID, and random instance identity. It carries no CSRF token or router control token. Because a runtime root permits only one live console, that console owns the only marker. Clean shutdown removes only the serving console's own marker.

Every API and SSE request is checked against a loopback `Origin` when one is present; a non-loopback origin gets a 403. There is no wildcard CORS and no caching header that would let a shared proxy keep a response. The console renders only an allowlisted set of fields from routers and registry files, treats those files as untrusted input, and validates their schema and size before using them. It never renders prompt or response text, provider error bodies, tokens, or account identifiers.

Each router's registry file carries a private control token, at least 256 random bits, stored only in that mode-0600 file. The token lets the console pin or unpin that one router's live session; it does nothing else, and the router never accepts a chain edit, a process operation, or a generic action through it. The token, and the browser's separate per-process CSRF token, never appear in diagnostics, console JSON, page state, logs, events, reports, MCP responses, or WebMCP.

Browser mutations, meaning anything a person does by clicking Approve, Reject, editing a proposal, or saving the chain editor, require an exact same-origin request and that CSRF token, which is injected only into the served root page and never returned by a JSON endpoint. Agent-facing channels, the MCP server, the HTTP tool endpoints, and WebMCP, can only read state and create proposals; none of them can approve, reject, pin, unpin, resume, or otherwise write to a router.

The console never restarts, signals, or writes to a router or a session outside of an approved proposal or a direct pin. It does not read prompts or responses, and it is not a security boundary against software already running with your local account privileges: such software can read your files, call the CLI, or automate your browser the same way the console does.

The installer copies the console's static site through a same-parent staging directory and keeps one recoverable managed backup container while replacing an existing managed site. A later installer can restore one validated backup after an interrupted update. Unsafe, ambiguous, writable, reparse-point or symlinked, and malformed backup state fails closed; it is not followed, replaced, or treated as managed. POSIX ownership checks refuse a backup owned by another user. Windows relies on the user-owned install root and reparse refusal; it does not apply POSIX ownership checks. The managed bundle marker is written only after the site and every other managed component have succeeded.

## What installation changes

The installer changes only recognized Airlock managed files and the optional managed worker. It refuses unknown files and links. It writes the managed bundle marker after all protected launchers, helpers, catalogs, and plugin files are copied. Startup rejects a stale, changed, missing, or incomplete bundle.

The session plugin is passed only to `airlock`. It is not registered globally.

The installer does not change native Claude, native Codex, global settings, global hooks, registered plugins, MCP configuration, or unrelated agents.

Already running sessions keep their original permissions. Restart after a security update.

## Limits that remain

- Anthropic does not officially support non-Claude models behind a Claude Code gateway.
- GPT model IDs may not appear in Claude Code's gateway model discovery.
- Remote Control is unavailable behind a non-Anthropic base URL.
- A compromised local account, Claude Code, proxy, Git, Python, or operating system is outside this protection. This includes the operating system's own credential storage: a process already running as the same user can read the stored OpenRouter key the same way it could read Codex or Claude session state.
- Prompt rules and tool hooks reduce mistakes but do not make model behavior mathematically certain.
- Native Claude Code controls which tools subagents can use.
- Provider terms, billing rules, model access, and usage limits can change.
- Approving a proposal in the console is not a security boundary against software already running with your local account privileges; it separates normal agent tool access from the approval path, nothing more.

## More detail

Read the [threat model](docs/threat-model.md) for trusted parts, attack cases, and remaining limits.

Problems in OpenAI OAuth, protocol translation, or the OpenAI proxy should also be checked against [`raine/claude-code-proxy`](https://github.com/raine/claude-code-proxy) and reported under its security policy when they reproduce there.
