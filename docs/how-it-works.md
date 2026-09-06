# How Airlock works

Airlock is a launch and policy layer around Claude Code. Claude Code still owns the conversation, terminal, permissions, tools, Agent cards, background tasks, cancellation, and worktree lifecycle.

## Saved launch profiles

```bash
airlock                 # saved default profile
airlock openai          # saved OpenAI-only root
airlock grok            # saved Grok-only root
airlock hybrid          # saved hybrid root
airlock om ROUTE        # exact open-model-only root
airlock hybrid om:ROUTE # exact open-model root with mixed workers
```

The setup wizard writes `AIRLOCK_DEFAULT_PROFILE` and saves one root for each profile. New setups recommend hybrid with the reserved root `auto`, which resolves at launch: Fable when its access class is neither extra nor unavailable, otherwise Opus, otherwise Sonnet, checked against your local access policy at every start. `auto` never selects a model that needs confirmed extra usage, so no launch can begin metered spend on its own under any extra-usage policy. Existing configs without a saved hybrid root keep Sonnet, which is what both launchers already used; moving an existing root to `auto` is a deliberate choice. Existing configs without the profile key retain the original OpenAI-only bare command.

The OpenAI-only profile points Claude Code directly at the local `claude-code-proxy` endpoint on `127.0.0.1`. It starts with an enabled OpenAI model and exposes only enabled OpenAI Agent models. No mixed-provider router is needed.

An explicit OpenAI alias such as `airlock terra` always starts this profile. `airlock hybrid MODEL` always starts the mixed-provider profile.

The Grok-only profile works the same way. It points Claude Code at the same local proxy, which selects the Grok upstream from the model ID and its own Grok login. Codex and Grok never share a login even though they share the proxy.

Open-model roots are deliberately outside saved launch profiles. Airlock never selects one for bare `airlock`, `auto`, or a family seat. `airlock om ROUTE` starts a pure session on one declared route. `airlock hybrid om:ROUTE` starts the mixed profile with that same exact local route as the root. The `om:` namespace keeps local routes distinct from declared OpenRouter routes without probing either private registry.

## Hybrid routing

```bash
airlock hybrid sol
airlock hybrid opus
```

A hybrid session starts one temporary router on an unused `127.0.0.1` port. Claude Code uses that one address for the whole session.

The router reads only the top-level model ID needed for routing:

- exact enabled `gpt-*` IDs go to the local OpenAI proxy
- Claude Code's deterministic wire form also routes to the same provider for supported native Claude IDs, such as `claude-opus-5[1m]` becoming `claude-opus-5`; legacy GPT IDs ending in `[1m]` are normalized to bare IDs before routing
- exact enabled `claude-*` IDs go to `https://api.anthropic.com`
- exact enabled `grok-*` IDs go to the same local proxy as GPT, which picks the Grok upstream
- an explicitly enabled `openmodel/ROUTE` goes directly to the route's signed loopback Chat Completions endpoint
- unknown or disabled IDs fail closed

The router registers a wire form only for an enabled full model ID. It does not accept arbitrary aliases. Existing Anthropic, OpenAI, Grok, and OpenRouter request behavior remains provider-specific. An open-model request is instead translated through a strict allowlist and is never forwarded as the original Anthropic body. Streaming responses are validated and translated into Anthropic events before Claude Code receives them.

On an Anthropic route, the router preserves Claude Code capability and Agent attribution headers. It forwards saved Claude login authorization opaquely to Anthropic.

On an OpenAI route, it removes incoming Claude authorization, API-key, cookie, proxy authorization, and OAuth capability headers before calling the local proxy. Claude credentials are never sent to OpenAI.

The router binds to loopback, has a parent-process lifetime, rejects redirects, and does not log prompts, response bodies, or credentials. Its local `/diagnostics` endpoint exposes a bounded in-memory event list plus cumulative per-provider/model request and token totals for the current session. Token counts are read from the response that was already forwarded, so observation never changes, delays, or buffers the stream. When an upstream sends no usage, the event has no token counts and the summary records that usage was absent rather than inventing a zero.

When an upstream answers HTTP 402, 429, or 529 and the session carries failover chains, the router retries that one request on an enabled same-cost-category peer, marks the limited model with a short cooldown, and reports what happened through the diagnostics outcomes. The chains are frozen into the session snapshot from the saved policy, so routing decisions never read credentials or make new policy calls mid-session. A user-owned `failover.json` can replace the derived chain for any model it names, including peers from other categories. See [Models and usage](models-and-usage.md) for the category rules, declared chains, and the `airlock mode failover` policy.

A peer that rejects the request only because the conversation is larger than its context window is handled the same way: the router recognizes that rejection, keeps walking the chain past models known to be too small, and when nothing fits it condenses older history through the destination provider's own economy worker, or trims it, before one retry. See [Models and usage](models-and-usage.md) for the order, the cost, and the `airlock mode overflow` setting.

## Native Agents

Every enabled named worker is a real Claude Code Agent.

Examples:

```text
airlock-astra
airlock-sol
airlock-terra
airlock-luna
airlock-luna-fast
airlock-opus
airlock-sonnet
airlock-fable
airlock-haiku
airlock-grok
airlock-composer
airlock-om-local-coder
```

Each name has:

- one exact full model ID
- the session effort level, unless you pin one
- a direct technical prompt
- Claude Code's normal subagent tool pool
- native background execution and cancellation
- native Agent cards and usage
- optional native worktree isolation

Named workers cannot invoke Agent at the default Agent depth of 1, and a caller cannot override their model. This keeps the card name, model, and role consistent. Subscription workers follow the session effort unless the config pins them. Open-model workers carry no Airlock effort or thinking setting because those capabilities are not inferred for a user-hosted model.

A worker runs inside Claude Code itself, not inside a shell command. A long response cannot be lost to a shell timeout.

## OpenRouter routes (optional)

OpenRouter is a separate, opt-in path for models Airlock does not otherwise offer. Setup never turns it on. It stays off until you both store a key and declare at least one route:

```bash
airlock openrouter auth set-key
airlock openrouter auth status
airlock openrouter auth logout
airlock openrouter models list
airlock openrouter models presets
airlock openrouter models add-preset NAME
airlock openrouter models add ROUTE MODEL ENDPOINT
airlock openrouter models remove ROUTE
airlock openrouter models refresh
airlock opr [ROUTE] [Claude arguments...]
```

`airlock openrouter auth` and `airlock openrouter models` manage the credential and the local registry; neither one starts a Claude Code session by itself. `airlock opr` is the command that starts one, using a route you already declared with `airlock openrouter models add` or `add-preset`.

`airlock openrouter auth set-key` stores your OpenRouter API key using the operating system's own credential protection: the macOS Keychain, Windows DPAPI scoped to your current Windows user, or the Linux Secret Service through `secret-tool`. There is no plaintext file fallback. If none of those backends is available, the command fails instead of writing the key to disk unprotected. There is no OAuth step; you bring the key from your own OpenRouter account.

`airlock openrouter models presets` lists curated, opt-in starting points for Kimi K3, DeepSeek V4 Flash 0731, Qwen 3.6 27B, and Ox Alpha without accessing the network or changing the registry. `add-preset NAME` expands one managed preset into its exact model, canonical provenance, default route, and pinned endpoint, then runs the same confirmed public catalog verification as a manual add. Installation and setup never add these routes automatically. Their suggested uses and tradeoffs are project-controlled summaries of community reports, not benchmarks or guarantees, and Airlock never accepts custom prompt text through the preset or registry commands.

`airlock openrouter models add ROUTE MODEL ENDPOINT` declares one route in a small local registry file. ROUTE becomes the Agent name `airlock-or-ROUTE`. MODEL must be the exact routable OpenRouter model ID returned as `data.id`, such as `anthropic/claude-sonnet-4.5`. Airlock rejects any catalog entry that declares a non-null `alias_target`; OpenRouter may omit that field for exact routes. Dynamic identities such as `:free` or `:extended` variants and `auto` or `latest` selectors are rejected independently. ENDPOINT must be the exact serving endpoint tag OpenRouter reports for that model, such as `anthropic` or `deepinfra/turbo`.

Adding or refreshing a route fetches public model and endpoint metadata from `openrouter.ai`, without sending your key, and asks for confirmation by name unless you pass `--yes`. Airlock checks that both catalog responses identify the requested routable model, that the named endpoint actually serves it, and that both the model and the endpoint report `tools` and `tool_choice` support, since named workers rely on real tool calls. It freezes the endpoint tag, catalog provider name, provider-registry routing slug, endpoint quantization, and separate `canonical_slug`. The provider name must map to exactly one routing slug, and the provider plus quantization must identify only that one endpoint in the model's current catalog. If any frozen identity changes, refresh stops rather than silently accepting the remap. The credential-free session snapshot carries the bounded routing values and canonical slug to the router; guidance text and credentials remain excluded. A preset freezes the same values, so a changed preset identity requires an Airlock update before it can be added. A route that fails any check is not added. `airlock openrouter models refresh` re-checks declared routes and reports what changed; it never rewrites the saved registry by itself, so add `--apply` once you are ready to save the refreshed metadata. The registry holds at most 10 declared routes. Preset endpoints were selected from low-priced eligible endpoints when the preset catalog was reviewed, but provider prices and availability can change; Airlock never silently changes the saved endpoint or claims it will remain cheapest.

The registry also records when each route was last verified. A route older than 30 days is treated as stale. Refresh it with `airlock openrouter models refresh --apply` before starting a session: a stale or otherwise invalid registry file stops every Airlock session, not only ones using OpenRouter.

A declared route becomes a named Agent, `airlock-or-ROUTE`, inside a hybrid session (`airlock hybrid ...`), where it can run alongside your OpenAI and Claude workers. `airlock openai`, `airlock grok`, and their pure profiles never include OpenRouter routes.

A declared route can also lead the mixed-provider profile itself. `airlock hybrid ROUTE` accepts an exact declared route name, and `airlock hybrid choose` offers declared routes for that launch without changing the saved default. The guided setup saves only built-in hybrid aliases or `auto`. The OpenRouter-rooted launch starts the same session router as any other hybrid root, adds the selected OpenRouter model as the root, and keeps every other family slot wrapper backed: Sonnet, Opus, Sol, Terra, Luna, Grok, and the rest keep their exact models. No OpenRouter model ever fills a Claude Code family alias slot, so built-in Agent spawning stays intact. The route you pick is not gated by `AIRLOCK_EXTRA_USAGE_POLICY`, exactly like an explicit `opr` root, while every other declared route in that session still follows it.

The reserved `auto` hybrid root resolves only among Fable, Opus, and Sonnet. Declaring or selecting an OpenRouter root never changes what `auto` launches.

A declared route can also become the exclusive session root with `airlock opr`:

```bash
airlock opr                       # interactive picker over declared routes
airlock opr kimi-k3               # exact declared route as the session root
airlock opr kimi-k3 -r            # resume with an exact declared root
```

Give `airlock opr` the exact route name, or omit it in an interactive terminal to pick from the offline local registry (this reads only the file on disk; it does not contact OpenRouter). Outside an interactive terminal, `airlock opr` requires an explicit route rather than prompting. An unrecognized, misspelled, or disabled route fails closed with an error before any request is sent; nothing falls back to a different route or provider. `airlock opr` never saves a default route: every launch either names one explicitly or asks, and running it does not change guided setup, the saved profile, or any `airlock mode` setting.

An `airlock opr` session is OpenRouter-only. It does not start, require, or read the OpenAI subscription proxy, and it does not touch Claude or Grok OAuth state; only the stored OpenRouter key and the declared route matter. `--model` and `-m` cannot be passed to `airlock opr`, because the root is selected by exact registry route, not by a model ID you type yourself.

Every request, whether to the `opr` root or to a hybrid-session `airlock-or-ROUTE` worker, constrains OpenRouter to the verified provider-registry slug and endpoint quantization, with fallback routing turned off. Airlock rejects a catalog where that pair identifies more than one endpoint. It requires catalog support for `tools` and `tool_choice`, but it does not require the endpoint to advertise every optional field in Claude Code's native Messages payload. Claude Code may put its custom-model notice in a `messages` entry with the non-standard `system` role. Before an OpenRouter request is sent, Airlock moves that text into the Anthropic Messages API's top-level `system` field so strict endpoints receive the same instruction in the documented form. Other native fields remain unchanged. A successful response is forwarded only when its model is the exact routable ID or the frozen canonical slug; a response for any other model is rejected rather than passed through. OpenRouter's `count_tokens` operation is not available on these routes; Airlock does not invent a token estimate for a model it has not verified.

Because Airlock does not evaluate an OpenRouter model's real capability, context window, or cost, an OpenRouter Agent is normally treated as extra usage: under the default `ask` policy it needs the same `Extra usage authorized: yes` confirmation as any other extra-usage worker before it can run, under `allow` it runs without asking, and under `never` it is not offered at all. The route you explicitly select as the `airlock opr` root, or as the hybrid OpenRouter root with `airlock hybrid ROUTE`, is the one exception: it carries the session's normal root traffic and is not itself gated by `AIRLOCK_EXTRA_USAGE_POLICY`, the same way an explicit hybrid root such as `airlock hybrid opus` is not treated as extra usage. If other routes are also declared, they can still appear as additional `airlock-or-ROUTE` Agents inside that same session, and those additional routes follow the normal extra-usage policy.

A forwarded `--model` or `-m` must agree exactly with the selected OpenRouter root route's model in a hybrid OpenRouter-rooted session, the way `airlock opr` rejects model overrides outright. A disagreeing value fails closed before any request is sent.

Read [Security](../SECURITY.md) and the [threat model](threat-model.md) for the credential storage and registry trust boundary.

## Loopback open-model routes (optional)

Open-model support is a separate provider family. It does not reuse the OpenAI subscription proxy or OpenRouter credential path. Installation declares nothing. The user first registers an already-running OpenAI Chat Completions endpoint and one or more routes with `airlock open-model`.

An endpoint URL has one accepted form: `http://127.0.0.1:PORT/v1`. A route contains a private upstream model identity, exact accepted response identities, its context and output ceilings, streaming and function-tool declarations, and whether it should become a named worker. Airlock treats those capabilities as user-declared and unverified. One endpoint owns one concurrency limit, so aliases for the same inference server share the same semaphore for their complete requests and streams.

The private URL and upstream identity stay out of process arguments: add commands use bounded hidden prompts or bounded exact-schema JSON on stdin. The values then stay in the protected `openmodel-registry.json`. Public surfaces use only `openmodel/ROUTE` and `airlock-om-ROUTE`. At launch, access policy freezes the exact active endpoints and routes into the protected signed session snapshot. The router uses only that snapshot and never rereads a changed registry. Ordinary output, guidance, Agent definitions, diagnostics, and router errors do not include either private value.

The router connects directly with `http.client.HTTPConnection` to the literal loopback port. It does not consult proxy environment settings, forward incoming credentials or cookies, add authorization, accept redirects, or send arbitrary configured headers. A pure `airlock om ROUTE` session starts only this temporary Airlock router and Claude Code. A hybrid local root starts the non-local services required by its ordinary worker pool as well.

Before any request can leave the router, the adapter builds a new OpenAI Chat Completions body from allowed Anthropic Messages fields. It handles system and message text, assistant function calls, successful user tool results, client function definitions, declared tool-choice modes, supported sampling values, stop sequences, streaming, and the route's output ceiling. It preserves ordering that Chat Completions can represent. Every pending assistant tool call must receive one immediate result before any user text or later message. Failed, missing, duplicate, late, or interrupted tool results and assistant text after a tool call fail locally rather than being reordered or weakened, as do unsupported semantic fields, undeclared tool behavior, malformed histories, images, documents, audio, server tools, thinking blocks, and redacted-thinking blocks.

Responses must carry one of the route's exact private identities. The adapter maps text, function calls, finish reasons, and token usage back to Anthropic Messages. It requires bounded strict JSON objects for function arguments and discards `reasoning_content` without logging it. For SSE, it reassembles fragmented tool calls by index and bounds every line, argument, tool count, and total buffered state. It does not commit a successful downstream stream until an identity-bearing event passes validation, and it delays final events until the usage event or `[DONE]`. A malformed event after commitment becomes a sanitized in-stream error, never a retry on another model.

The MVP accepts only `/v1/messages` for local routes. It does not implement `/v1/messages/count_tokens`, the OpenAI Responses API, native Ollama APIs, images, documents, audio, embeddings, or server-side tools. Airlock does not download a model, configure hardware, select inference flags, or manage the server process.

Open models are explicit only. They never enter saved `auto`, family or discovery seats in a hybrid session, handoff, provider fallback, capacity routing, synthesized compactors, or automatic armies. A pure local root may bind Claude Code's family aliases to that one explicitly selected model because no other model exists in that profile. A hybrid local root keeps its ordinary non-local family and discovery seats.

## Built-in Explore, Plan, and general-purpose

Airlock keeps the exact built-in Agent types:

- Explore for read-only repository discovery
- Plan for read-only technical design
- general-purpose for multi-step work

Plan and general-purpose inherit the orchestrator when the call omits `model`. Routine Explore should use the `haiku` family slot from the generated session guidance. Pure OpenAI and Grok profiles resolve that slot to their economical discovery model. A pure open-model profile maps every family alias to its one explicitly selected root. Hybrid profiles, including a hybrid open-model root, keep a recognized Claude model in the Haiku slot so Claude Code's built-in WebFetch continues to work; the separate `AIRLOCK_DISCOVERY_MODEL` channel can still name a cheaper non-local route.

For one built-in call, the main model may pass a Claude Code family alias:

```text
Agent(subagent_type="Explore", model="haiku", ...)
Agent(subagent_type="Plan", model="opus", ...)
```

The session guard resolves the alias and checks its exact target before Claude Code starts the Agent.

- Every profile defines all four schema-valid aliases: `fable`, `opus`, `sonnet`, and `haiku`.
- Every alias points to an exact model already enabled in that session.
- The `haiku` alias points to the economical discovery model in pure OpenAI and Grok profiles. Hybrid profiles use the cheapest enabled Claude route there so Claude Code accepts the slot for built-in background work and WebFetch.
- Disabled, cross-profile, confirmation-required, and ineligible Fast routes cannot appear behind an alias.
- Omitting `model` keeps normal inheritance for Plan and general-purpose. Explore also inherits when the root is already the economical discovery route; otherwise routine unpinned Explore is rejected with `model="haiku"` and the exact resolved target.
- Named `airlock-*` Agents remain the exact-model interface.

A named Agent has no per-call effort field. It follows the session effort by default, and `/effort` can move it in the middle of a session. A configured pin stays fixed until the config changes.

## Model selection and `/model`

Every profile binds Claude Code's Fable, Opus, Sonnet, and Haiku slots to exact models in the active policy. This prevents inherited shell values from sending a built-in Agent to a disabled or cross-provider route:

- OpenAI Fable and Opus use Sol, Sonnet uses Terra, and Haiku uses Luna. When Astra is enabled it takes the Fable and Opus slots ahead of Sol. A missing route falls back to the closest enabled OpenAI model.
- Grok Fable and Opus use Grok 4.6, while Sonnet and Haiku use Composer. A missing route falls back to the enabled Grok model.
- Hybrid profiles use the strongest eligible route for Fable, Opus, and Sonnet. Haiku uses the cheapest enabled Claude route so built-in background work remains recognized, while `AIRLOCK_DISCOVERY_MODEL` keeps the separate economical route available to session guidance. A hybrid open-model root keeps these non-local seats unchanged.
- A pure open-model profile maps all four aliases to the exact route the user selected for that launch. This is local inheritance, not automatic discovery or a capability claim.
- A route that still needs explicit extra-usage confirmation is not placed behind a family alias because an alias has no way to carry Airlock's confirmation marker.
- The exact root named on the launch command remains available as the custom option, and named `airlock-*` Agents keep their exact model identities.

These are Claude Code family slots, not claims that GPT-6 Astra or GPT-5.6 Sol is Claude Opus or that Composer is Claude Haiku. Airlock sets each label to the exact model ID so the menu reports what will actually receive the request.

The hybrid router can route both providers because every model uses the same local endpoint. This does not guarantee that every GPT ID appears in Claude Code's `/model` menu. Claude Code gateway discovery can ignore non-Claude IDs.

Use these reliable paths:

- start the saved root with `airlock`, the OpenAI-only root with `airlock openai`, an exact subscription root with `airlock terra` or `airlock hybrid MODEL`, or an exact local root with `airlock om ROUTE` or `airlock hybrid om:ROUTE`
- use an exact named `airlock-*` Agent
- pass a schema-valid `fable`, `opus`, `sonnet`, or `haiku` family alias to Explore, Plan, or general-purpose

A model ID that the router does not allow cannot be used even if Claude Code accepts the text.

Remote Control is unavailable behind a non-Anthropic `ANTHROPIC_BASE_URL`.

## How the main model chooses an approach

| Situation | Usual choice |
|---|---|
| Small, connected, already understood, integration, or final synthesis work | Work directly |
| Bounded read-only repository discovery | Explore |
| Read-only design after enough code is known | Plan |
| Multi-step work that fits one model | general-purpose |
| One focused task that benefits from an exact model | One named Agent |
| Many independent high-volume tasks | A native Luna batch |
| Difficult visual, product-flow, or interaction judgment in hybrid | Opus |
| Bounded design-system implementation or refinement | Sonnet |

Before doing a multi-part request directly, the main model separates independent parts by skill. For example, a request that combines keyboard interaction design with filesystem behavior sends the interaction portion to Opus and keeps the filesystem portion with the root or its best implementation route. The root then integrates and verifies both. Users do not need to request this split. Small single-role changes still stay direct when another worker would add overhead.

A worker query should contain the goal, useful repository paths, constraints, settled decisions, allowed side effects, evidence, and acceptance checks. It should not add transport forms, task-kind labels, or JSON response requirements unless the user needs that exact output.

Claude Code Workflow stays out of this table on purpose. The main model uses it only when you ask for multi-agent orchestration directly. Native Agent fan-out covers every other case, and mixing the two would put a second scheduler on top of one that already works.

## Luna armies

Automatic armies use native Agent fan-out. The main model launches a useful group before waiting, then collects every result.

Automatic armies may use only:

- `airlock-luna`
- `airlock-luna-fast` when plan and proxy checks allow it
- `airlock-composer` when Grok is enabled

Both run at the session effort. Pin them with `AIRLOCK_EFFORT_LUNA=max` if you want armies to think harder than the rest of the session.

Good army tasks are independent search shards, webpage reading, extraction, lookup, summarization, broad discovery, and test or log triage.

Luna can also implement code when each shard has:

- explicit file ownership
- clear no-touch boundaries
- no dependency on another shard
- acceptance checks

A stronger Sol, Opus, Grok, or capable main model reviews, integrates, tests, and synthesizes the full result. Astra, Sol, Terra, Opus, Sonnet, Fable, Haiku, Grok 4.6, and user-declared open models are not multiplied automatically.

A session is only told about the routes it actually enabled. A Grok-only session is not given Luna army instructions, and a session with no economical high-volume route is told plainly that it has no automatic swarm route.

## Concurrency

```bash
airlock mode max-agents off
airlock mode max-agents 3
```

`off` removes the Airlock cap and uses Claude Code's native limit. It does not mean unlimited work and it is not a reason to start unnecessary Agents.

A number saves a smaller cap for new sessions. This limits Claude Code Agent work. It is separate from each open-model endpoint's declared `max_concurrency`, which the router enforces across every route alias for that endpoint and holds until the request or stream is fully closed.

Agent depth is 1 by default. Named workers disallow the Agent tool, so fan-out stays with the main model, which prevents hidden Agent trees and keeps usage visible. `airlock mode depth 2` opts into one more level. At depth 2 a named Agent may invoke Agent, but only to spawn its own Agent type, so every descendant runs the model the root chose for that worker. A caller still cannot override a worker's model, and the guard denies a mismatched type or a model override on a nested call.

## Session-local OpenAI Fast handoff

The direct shortcut `airlock fast -r` starts a one-session root on fixed `gpt-5.6-sol-fast` through the Codex proxy. It leaves saved `AIRLOCK_OPENAI_FAST` unchanged. Eligible OpenAI plan and proxy checks still apply, and the route has no fallback.

The in-session workflow is `/airlock-fast`. The managed skill arms the current session only. After a clean user exit, the owning launcher resumes the exact conversation once on `gpt-5.6-sol-fast`. This is not Claude Code Anthropic `/fast`. Hard kills, crashes, non-clean exits, SessionEnd hook failures, and expiry intentionally stop the handoff rather than relaunching.

The handoff marker is private and bound to a nonce, launcher PID, cwd, and session ID. It does not parse transcripts or carry credentials, prompts, arbitrary executable data, or a model choice. The managed plugin's SessionEnd hook is required and authorized as part of Airlock's plugin, not global hooks.

## Native worktree snapshots

A main model can request `isolation: "worktree"` for an implementation Agent.

The session-scoped WorktreeCreate hook replaces default Git worktree creation. It builds a clean synthetic snapshot from the current checkout without changing the user's branch, index, staging area, or files.

The snapshot includes:

- current tracked files
- staged and unstaged tracked changes
- eligible non-ignored untracked regular files
- key-only projections for tracked or eligible env files

It filters:

- known credential paths
- JSON files with known credential fields
- complete private-key blocks
- ignored files
- unsafe links and Windows reparse points
- special files and paths outside the repository
- oversized or unstable content

The snapshot starts as one isolated commit so native cleanup can tell whether the Agent changed or committed anything. A custom WorktreeRemove hook removes only an unchanged managed worktree. It preserves a worktree with edits or commits.

Worktree creation has no task prompt, so it cannot decide that one omitted file is unimportant. It fails when eligible input exceeds its safety bounds instead of silently giving the Agent a misleading partial checkout.

## Direct file guards

The session plugin runs before Read, Grep, Glob, and Bash.

It returns a key-only projection for an exact env read. It blocks known credential paths, credential-bearing JSON, complete private-key blocks, broad searches that could return them, and obvious shell references to those paths.

This is a practical safety layer, not a full shell sandbox. A model with Bash can run repository code. A tracked secret may already exist in shared Git history. Do not commit credentials and use stronger operating-system isolation for untrusted repositories.

## Cached update notices

Only `airlock update --check` queries GitHub for an update notice. When it finds a newer release, the updater atomically writes a bounded JSON record under the Airlock config root. The record contains only the check time, installed and available versions, and the canonical Airlock release page.

A managed SessionStart hook reads that file on a new, resumed, or cleared Airlock session. It validates the file type, size, schema, versions, release URL, installed-version match, and seven-day lifetime before returning Claude Code's user-only `systemMessage` field. It never returns `additionalContext`, so the notice is not added to the model context. Missing, unsafe, stale, or malformed state produces no output. No network request occurs during startup.

## Context sizing

Claude Code treats `CLAUDE_CODE_AUTO_COMPACT_WINDOW` as one process-wide compaction trigger. Native Anthropic roots already have model-aware sizing, so Airlock leaves them unset. The authorized Sol proof above 300,000 tokens did not pass on 2026-08-09, so OpenAI roots and Grok roots without a documented window keep the saved conservative fallback instead of claiming an unverified 1M path. `grok-4.6` is different: Airlock declares its documented 500,000-token hard ceiling and uses 400,000 as the default trigger. For roots without a known or declared hard ceiling, Airlock drops any inherited `CLAUDE_CODE_MAX_CONTEXT_TOKENS` so a stale value cannot silently cap the session's workers.

A user-exported `CLAUDE_CODE_AUTO_COMPACT_WINDOW` has highest priority as the requested trigger. An explicitly exported numeric `AIRLOCK_CONTEXT_WINDOW` has the next priority, while `AIRLOCK_CONTEXT_WINDOW=auto` removes the explicit trigger. Any explicit numeric trigger applies to the root and every worker because Claude Code has no per-Agent compaction variable.

Every open-model root also exports its exact route's user-declared `context_window` as `CLAUDE_CODE_MAX_CONTEXT_TOKENS`. This is a hard ceiling, not merely a default trigger, and it remains in force when either compaction override is present. A route declaration may be from 1,000 to 10,000,000 tokens. Airlock uses it as the explicit trigger only when it is within Claude Code's accepted 100,000 to 1,000,000 range; otherwise Claude Code's unknown-model window enforcement owns the ceiling. An override can move the requested compaction point, but it cannot raise the effective threshold above the declared ceiling. `CLAUDE_CODE_MAX_CONTEXT_TOKENS` is process-wide too, so a hybrid local root's declared ceiling also caps every non-local worker in that session. Claude Code has no per-Agent hard-window control that Airlock can use to give those workers a larger ceiling while retaining native Agents. Airlock treats every local declaration as user-provided and does not probe or infer it.

## Usage and failure handling

`airlock usage` reads OpenAI plan windows through the documented Codex app-server method. It does not send a model request.

Inside a hybrid session, `airlock session-usage` reads only the active loopback router's cumulative summary. It reports Anthropic, OpenAI, and Grok requests, completed and failed outcomes, observed-usage events, input, cache-write, cache-read, and output totals. OpenRouter groups are always omitted from this summary because OpenRouter belongs to a separate account and this command does not claim to report its usage; this applies the same way whether the OpenRouter traffic came from a hybrid-session `airlock-or-ROUTE` worker or from an `airlock opr` root. The command rejects non-loopback and deceptive URLs, never emits diagnostic event bodies, and labels the result as provider-reported session usage rather than a bill. `airlock openai` and `airlock grok` have no session router, so the command fails clearly instead of guessing. An `airlock opr` session does start its own session router the same way a hybrid session does, but since every request there goes to OpenRouter, `airlock session-usage` in an `opr` session reports no groups.

Claude Code's native Agent card can still show zero tokens for custom OpenAI or Grok IDs. Airlock does not spoof a Claude ID or rewrite provider bytes to change that closed-source display. `airlock session-usage` is the accurate Airlock-owned view when the provider returned usage.

Grok has no equivalent readable plan window, so Airlock reports Grok headroom as unknown rather than guessing. It does record whether the proxy holds a Grok login, and a session that confirms it is signed out disables the Grok routes instead of advertising workers that would fail on their first request.

Use native Claude Code's `/usage` screen for Anthropic subscription bars. Airlock does not read Claude login files or guess Anthropic percentages.

An explicit provider or model never silently changes. The router returns upstream status and error bodies to Claude Code so native retries can work, but an unknown route fails locally.

## The console

`airlock console` starts a local, loopback-only server that shows every Airlock session running on the machine. It is the only process that aggregates across sessions; each router still only knows about itself.

### Console address marker

Only one console can serve from one runtime root. It holds a private `console.lock` beside the address marker for its process lifetime. A second start exits safely; when the marker validates, the launcher opens the existing console instead. Operating-system lock release permits recovery after a hard crash. A stale non-secret marker may remain after a crash, and the next start that obtains the lock replaces it. Different runtime roots are independent.

While the console is serving, on its default port or a port chosen with `--port`, it atomically writes a private, non-secret `<console runtime root>/console-address.json` file. The root is `%LOCALAPPDATA%\Airlock\` on Windows and `$XDG_STATE_HOME/airlock/` elsewhere (default `~/.local/state/airlock/`). Its bounded schema contains a schema version, the console's loopback URL, process ID, and random instance identity. It carries no CSRF token or router control token. Because a runtime root permits only one live console, that console owns the only marker. On clean shutdown the console removes only the marker that matches its own instance.

`airlock-console-tools` rereads this marker for every call, so it follows a custom-port console automatically. If the marker is absent or stale, it falls back to `http://127.0.0.1:4783`. An explicit MCP `--console-url` bypasses discovery.

### The session registry

When a hybrid, `opr`, or open-model router becomes ready, it writes one small JSON file describing itself into a shared registry directory: on Windows `%LOCALAPPDATA%\Airlock\sessions\`, elsewhere `$XDG_STATE_HOME/airlock/sessions/` (default `~/.local/state/airlock/sessions/`), a file mode 0600 where the platform supports it. The file names the router's instance ID, its loopback URL, the owning and router process IDs, when it started, its profile and root model and provider, and the working directory its launcher started in. The router removes the file when it shuts down.

The console treats a registry entry as ended, and removes it, once its owning process is gone or its URL no longer answers `/healthz` with a matching instance ID. `--scan` additionally probes local ports for a router that has no registry file, for the rare case where one predates this feature or its file was lost.

### Diagnostics additions

The router's existing `/diagnostics` endpoint gained fields the console needs and nothing else does: when the router started, its owner process ID, its working directory, the time of its last request, its currently pinned model if any, the full route table with each route's category and context window, active cooldowns, the frozen per-model handoff chains, and the most recent foreground request's observed context size. None of this is prompt or response content; it is the same kind of route and outcome metadata the diagnostics endpoint already reported.

### Session pinning

A handoff proposal, once approved, or a direct pin from the console, retargets one session's live router to a chosen model for its future requests. This does not restart Claude Code or the session. The conversation, process, worktree, and task continue exactly as before; only where the next request is sent changes. Background and compaction requests are retargeted along with foreground ones. The pin uses the pinned model's own frozen failover chain, so a temporary automatic failover away from the pin does not change what the pin is. Unpinning restores the session's normal root and background routing. A pin is refused when its target is cooling or, if both the observed conversation size and the target's window are known, too large for it; an unknown size or window does not block the pin, but is reported honestly rather than assumed to fit.

### The console tools MCP server

`airlock-console-tools` is a second, separate stdio MCP server from `airlock-web-tools`. It is a thin standard-library client to the console's own HTTP API; it does not read router registry files or talk to a router directly. It rereads the console address marker on every call, falls back to port 4783 when it is absent or stale, and lets an explicit MCP `--console-url` bypass discovery. It ships the same seventeen read, query, and propose tools to Claude Code, Codex, or any other MCP client, and the console page registers the same tools through WebMCP. There is no approve, reject, pin, unpin, or router-control tool on any of these surfaces; only a person clicking Approve in the browser can apply a proposed change. See the [console guide](console.md#agents) for the tool list.

## Console site installation

The installer copies the built console page through a staging directory beside its final site directory. Replacing an existing managed site uses one recoverable managed backup container. An interrupted update restores one validated backup on the next installation. Unsafe, ambiguous, writable, reparse-point or symlinked, wrong-owner, and malformed backups fail closed and remain for inspection rather than being followed or overwritten. The managed bundle marker is written last, after the site swap and every other managed component succeeds.

## Managed bundle checks

The installer writes the managed bundle marker last. The launcher checks hashes for launchers, helpers, Agent catalogs, and the session plugin before starting.

```bash
airlock bundle
```

A stale, changed, missing, or incomplete managed file stops startup and asks for a reinstall and fresh session.

## Support boundary

Anthropic documents Claude Code gateways and subscription login forwarding. Anthropic does not officially support non-Claude models behind those gateways. Airlock is an independent compatibility layer, so provider or Claude Code updates can affect mixed routing.

The project keeps protocol-level tests for the router, the guards, and the worktree hooks, and it checks live behavior separately before each release. See [Testing](testing.md) and [Live tests](live-tests.md).
