# Models, limits, and usage

Airlock does not force one model for every task. You choose which routes are enabled, then the main model picks the smallest useful approach. An exact user choice wins.

## Model roles

These descriptions guide selection. They are not vendor rankings.

| Worker | Best fit | Relative plan use |
|---|---|---|
| Sol | Difficult implementation, cross-file integration, backend and API work, test-driven repair, measured performance work, and difficult debugging | High |
| Terra | Adversarial review, independent second opinions, competing designs, and alternative debugging hypotheses | Medium |
| Luna | High-volume discovery, extraction, lookup, summarization, test or log triage, small mechanical work, and clearly bounded implementation | Low |
| Luna Fast | The same independent work with priority processing on eligible plans | Low, but faster consumption |
| Opus | Difficult architecture, UI and UX direction, product flows, new design systems, security reasoning, high-impact review, and final synthesis | High |
| Sonnet | Deep repository research, broad review, documentation, design-system implementation, iterative refinement, ambiguous debugging, and balanced implementation | Medium |
| Fable | An explicit focused Anthropic choice when enabled and authorized | Plan dependent |
| Haiku | An explicit bounded Anthropic utility choice when enabled | Low |
| Grok | Difficult implementation, tool-heavy coding, debugging, and synthesis on a Grok plan | High |
| Composer | High-volume discovery, extraction, lookup, summarization, triage, and bounded mechanical work | Low |

The active profile, enabled routes, Fast eligibility, and extra-usage policy still apply.

## Main model shortcuts

Start the profile and orchestrator saved by setup:

```bash
airlock
```

OpenAI-only sessions:

```bash
airlock openai
airlock terra
airlock luna
airlock 5.5
airlock 5.4
airlock mini
airlock spark
```

Mixed-provider sessions:

```bash
airlock hybrid
airlock hybrid choose
airlock hybrid auto
airlock hybrid sol
airlock hybrid terra
airlock hybrid luna
airlock hybrid opus
airlock hybrid sonnet
airlock hybrid fable
airlock hybrid haiku
airlock hybrid grok
airlock hybrid composer
```

Grok-only sessions:

```bash
airlock grok
airlock grok composer
```

Grok needs its own login with `airlock proxy grok auth login`, and its routes stay off until the saved configuration enables them or you name a Grok root. `airlock usage` does not cover Grok, because there is no documented plan-window method Airlock can read safely.

`airlock hybrid` uses the saved hybrid root. `airlock hybrid choose` opens the full picker. An explicit OpenAI alias such as `airlock terra` stays OpenAI-only even when bare `airlock` is saved as hybrid.

The saved root can be the reserved value `auto`, which is what a fresh setup writes. `auto` resolves at every launch under your local access policy: Fable when its access class is neither extra nor unavailable, otherwise Opus, otherwise Sonnet. It never selects a model that needs confirmed extra usage, so no launch under an ask or never policy can start metered spend on its own. An existing config that never had a saved hybrid root keeps Sonnet instead of moving to `auto` on its own; switching is a deliberate choice with `./scripts/setup.sh --hybrid-model auto` or `airlock hybrid auto`.

Model access depends on your account and can change. A model supported by the proxy may still be unavailable on your plan.

## Advanced: background command and utility model

These settings are not part of normal Agent routing.

- `airlock bg` is a separate convenience command with its own saved OpenAI model and starting effort. Normal named Agents do not run through it.
- The utility model handles lightweight Claude Code requests such as titles. The setup wizard chooses an economical default, and most users do not need to change it.

Both controls stay under Advanced in the setup wizard because they are optional implementation details, not choices required for a first session.

## Built-in Agent model choice

Plan and general-purpose inherit the orchestrator model when `model` is omitted. Routine Explore uses `model="haiku"`, which Airlock resolves to the economical discovery model. If an unpinned Explore would inherit a different premium root, the guard blocks it and gives that schema-valid retry plus the exact target.

A main model can give Explore, Plan, or general-purpose one of Claude Code's `fable`, `opus`, `sonnet`, or `haiku` family aliases. Airlock maps every alias to an exact enabled model and rejects invalid, disabled, cross-profile, confirmation-required, and ineligible Fast targets.

Named `airlock-*` Agents already have an exact model. They follow the session effort unless setup pins a level. Callers cannot override either value for one Agent call. Use a named Agent when exact model identity matters.

Claude Code may omit GPT IDs from `/model` discovery behind a gateway. Starting the exact root with `airlock` or `airlock hybrid`, or using a named Agent, is more reliable than depending on discovery.

## UI and UX routing

In a hybrid session, visual and interaction design is Anthropic-first and Opus-led. Opus is preferred for visual direction, product flows, new design systems, high-fidelity screens, broad redesigns, and final visual critique.

Sonnet fits bounded components, iterative refinement, and implementation that follows an existing design system. The main model does not start both by default.

For mixed UI and backend work, keep visual direction with Opus and split backend work only when it is independent. Verify rendering, screen sizes, interactions, and accessibility before accepting UI work when the available tools allow it.

## Evidence rules

Every result still needs evidence:

- Require a reproducer and causal explanation for backend bugs.
- Require before-and-after measurements for performance work.
- Require independent tools and manual verification for security work.
- Separate planning, implementation, and review for architecture and large refactors.
- Check citations, APIs, tests, migrations, and production assumptions.

## Routing modes

```bash
airlock mode
airlock mode economy
airlock mode balanced
airlock mode quality
airlock mode budget
airlock mode defaults
```

- Economy starts small and prefers lower-use workers.
- Balanced uses the best fit and may add one useful reviewer.
- Quality can use more workers for difficult independent work.
- Budget selects economy, blocks extra usage and automatic failover, and turns both provider Fast controls off.
- Defaults restores the tested settings.

## Top-level concurrency

```bash
airlock mode max-agents off
airlock mode max-agents 3
```

`off` is the default. It removes the Airlock cap and uses Claude Code's native limit. It is not permission to start unnecessary workers.

A number saves a smaller cap for new sessions. Agent depth is 1 by default, so named Agents cannot invoke Agent and fan-out stays visible at the root.

```bash
airlock mode depth 1
airlock mode depth 2
```

At depth 2 a named Agent may invoke Agent, but only to spawn its own Agent type, so every descendant runs the model the root chose for that worker. A caller still cannot override a worker's model, and the guard denies a mismatched type or a model override on a nested call. The depth is a real cap: Airlock sets Claude Code's own `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` to it, and the harness both enforces the limit and decides whether a worker receives the Agent tool at all. A worker at depth 2 cannot spawn a third level.

## Provider Fast controls

For a one-session root, use:

```bash
airlock fast -r
```

This starts fixed `gpt-5.6-sol-fast` through the Codex proxy without changing saved `AIRLOCK_OPENAI_FAST`. It requires the same eligible OpenAI plan and verified proxy checks as other OpenAI Fast routes and has no fallback. Inside a managed session, `/airlock-fast` arms a one-shot handoff; exit cleanly so the owning launcher can resume the exact conversation once on that root. This is not Claude Code Anthropic `/fast`. Hard kills, crashes, non-clean exits, hook failures, and expiry prevent relaunch. The managed plugin SessionEnd hook is required.

```bash
airlock mode fast all
airlock mode fast openai
airlock mode fast anthropic
airlock mode fast off
airlock mode openai-fast on
airlock mode anthropic-fast off
```

The four `airlock mode fast` choices set both provider controls together. The provider-specific commands change only one. `airlock mode defaults` turns both off, and `airlock mode budget` also turns both off.

OpenAI Fast permits eligible `sol-fast` and `luna-fast` routes. It does not rename a standard route or claim that an unsupported model became faster. Use `airlock sol-fast` for the explicit Fast root. Luna Fast can also be selected for an automatic army by the advanced policy below. Both paths still require an eligible sanitized OpenAI plan and verified proxy support.

Anthropic Fast is Claude Code's native Fast mode. Airlock enables it only when the exact session root is `claude-opus-5[1m]`; it never changes a Sonnet, Fable, or Haiku root into Opus. Anthropic Fast uses paid usage credits from the first token. With `AIRLOCK_EXTRA_USAGE_POLICY=ask`, an interactive launch asks first, or one noninteractive launch can set `AIRLOCK_ANTHROPIC_FAST_AUTHORIZED=yes`. `never` refuses the launch and `allow` starts it directly.

Unsupported models stay at standard speed. These settings are session-scoped and do not change global Claude Code settings.

## Luna armies

Automatic armies use Claude Code's native Agent fan-out. The main model starts a useful non-overlapping batch, lets the Agents run in the background, and collects every result before synthesis.

Automatic fan-out may use only Luna or eligible Luna Fast. They run at the session effort. If you want armies to think harder than the rest of the session, pin them with `AIRLOCK_EFFORT_LUNA=max`.

Good army work includes:

- independent repository searches
- public page reading when authorized
- extraction and lookup
- summarization
- broad discovery
- test or log triage
- implementation shards with explicit file ownership, no-touch boundaries, and acceptance checks

Do not automatically multiply Sol, Terra, Opus, Sonnet, Fable, or Haiku. Do not use armies for coupled edits, architecture, security judgment, cross-file integration, or final synthesis.

One stronger Sol, Opus, or capable main model reviews, integrates, tests, and synthesizes the complete Luna results.

## Luna Fast policy

```bash
airlock mode swarm-fast auto
airlock mode swarm-fast on
airlock mode swarm-fast off
```

The provider-wide OpenAI Fast control must be on before any Luna Fast selection can take effect.

- `auto` selects Luna Fast only when OpenAI Fast is on, the sanitized OpenAI plan is exactly `prolite` or `pro`, and proxy support is verified.
- `on` requires the same checks and fails closed when they are not satisfied.
- `off` always uses standard Luna.

Luna Fast uses `gpt-5.6-luna-fast`. It can consume plan capacity faster. API pricing changes do not guarantee more subscription quota.

Sol Fast is never selected for a swarm. It remains an explicit root-only choice and uses the same eligibility checks.

## Extra usage

```bash
airlock mode extra-usage ask
airlock mode extra-usage never
airlock mode extra-usage allow
```

`ask` is the default. A route marked as extra usage needs the exact confirmation line before its Agent starts:

```text
Extra usage authorized: yes
```

Provider billing settings are final. If paid credits are enabled on the account, a local routing preference cannot promise zero paid usage.

## Rate limit failover

```bash
airlock mode failover ask
airlock mode failover never
airlock mode failover allow
```

When an upstream behind the session router answers HTTP 429 or 529, Airlock immediately retries that request on another enabled model of the same cost category instead of hanging or failing. Premium models hand off among Opus, Sol, and Grok. Standard models hand off between Sonnet and Terra. Economy models hand off among Luna, Composer, and Haiku. Luna Fast sits in its own category, Fable stays alone because it is metered separately, and OpenRouter routes form their own category and can hand off only among themselves.

The replacement order inside a category is fixed and cheap first. A model that just answered 429 or 529 goes on a short cooldown: 60 seconds by default, or the provider's `Retry-After` hint bounded between 1 second and 5 minutes. Later requests skip every cooling route without spending a round trip. One request tries at most three hops. If every same-category peer is also limited, the client receives an honest 429 with a fixed Airlock message, and no upstream body is reflected.

Policies:

- `ask`, the default, builds chains among included models only. Automatic healing never starts a model that would need confirmed extra usage.
- `never` disables failover entirely and builds no chains.
- `allow` also lets extra-usage models act as failover targets for their own category.

`airlock mode budget` sets failover to `never` along with its other limits.

Inside an active hybrid session, `airlock session-usage` lists models that are currently cooling down. The router's local diagnostics record `upstream_rate_limited`, `rate_limit_cooldown_skip`, and `rate_limit_exhausted` outcomes, and completed requests carry the model they originally targeted under `failover_from`.

## OpenRouter routes

OpenRouter routes are not in the model roles table above because Airlock does not rank them. Add any supported exact route yourself with `airlock openrouter models add ROUTE MODEL ENDPOINT`, or start from one of four managed presets:

```bash
airlock openrouter models presets
airlock openrouter models add-preset kimi-k3
airlock openrouter models add-preset deepseek-v4-flash-0731
airlock openrouter models add-preset qwen-3-6-27b
airlock openrouter models add-preset ox-alpha
```

Listing presets is offline and changes nothing. Adding one still asks for confirmation and checks its exact model, frozen canonical provenance, pinned endpoint tag, catalog provider name, provider-registry routing slug, endpoint quantization, and required tool support against OpenRouter's public catalog before writing the registry. The provider name must map to one slug, and provider plus quantization must identify one endpoint. Requests use the slug and quantization constraints with fallback disabled. They require catalog support for `tools` and `tool_choice` without requiring the endpoint to advertise every optional Claude Code field. Responses must report either the exact routable model or its frozen canonical slug. The shipped suggestions are cautious summaries of community reports, not benchmarks or guarantees. They do not claim that an endpoint will remain cheapest, and their text never enters the registry, session snapshot, route policy, or Agent prompt.

The current preset guidance is:

| Preset | Suggested use | Reported tradeoffs |
|---|---|---|
| Kimi K3 | Frontend and visual implementation, plus substantial coding agents | Slower and token-heavy |
| DeepSeek V4 Flash 0731 | Cost-sensitive coding, debugging, and bounded repository automation | Harness-sensitive tool use and weaker non-coding reliability |
| Qwen 3.6 27B | Bounded coding, refactoring, planning, tests, and data work | Tool loops and long-session degradation |
| Ox Alpha | Long-horizon coding agents, multi-step tool loops, and repository-scale reasoning over a 1M-token context, with screenshots and logs alongside code | Anonymous preview provider that retains prompts and completions, free pricing and availability that can end without notice, reported tool-call errors near 4.5 percent, and single-run benchmark claims |

The Kimi K3, DeepSeek V4 Flash 0731, and Qwen 3.6 27B guidance was reviewed on 2026-08-10, and the Ox Alpha guidance on 2026-08-22. All of it remains unverified. Airlock does not accept custom descriptions, notes, or prompt text through either model command.

Ox Alpha is a stealth preview rather than a named vendor model. OpenRouter lists it at zero cost for prompt and completion tokens and routes it through a single `stealth` endpoint, and it says the anonymous provider retains prompts and completions without training on them. Treat it the way you would treat any route that sends your repository to a third party you cannot name: keep private or regulated code off it, and expect the free window to close. If the catalog entry is renamed or withdrawn when the provider is revealed, `airlock openrouter models refresh` fails for that route, and its verified metadata expires 30 days after the last check, which stops every Airlock session until you refresh it or run `airlock openrouter models remove ox-alpha`.

All OpenRouter routes stay off until you add one. A declared route can appear as a named `airlock-or-ROUTE` worker inside a hybrid session, alongside your OpenAI and Claude workers. It can start its own OpenRouter-only session as the exclusive root with `airlock opr ROUTE` (or `airlock opr` alone to pick one interactively from your declared routes). `airlock opr` never picks or saves a default route on its own.

A declared route can also lead a full mixed session as the hybrid root. Pass its declared route name directly, as in `airlock hybrid ox-alpha`, or select it for one launch with `airlock hybrid choose` option 10. Neither command changes the saved default; guided setup saves only a built-in hybrid alias or `auto`. The selected route behaves like an explicit `airlock opr` root: it carries normal session traffic without extra-usage gating. The difference is what surrounds it. Every other family slot stays wrapper backed, so Sonnet, Opus, Sol, Terra, Luna, Grok, and any other enabled worker keep their exact models, and every other declared OpenRouter route still follows `AIRLOCK_EXTRA_USAGE_POLICY` normally. An OpenRouter model never fills a Claude Code family slot, so named Agents and family aliases keep working.

Because Airlock does not evaluate a declared model's real capability, context window, or cost, an OpenRouter route usually follows `AIRLOCK_EXTRA_USAGE_POLICY` like any other extra-usage worker: it needs `Extra usage authorized: yes` under `ask`, runs without asking under `allow`, and is not offered under `never`. The one route you explicitly select as the `airlock opr` or hybrid OpenRouter root is the exception: it carries that session's normal traffic and is not itself gated by the extra-usage policy, the same as an explicit hybrid root. Any other declared route that becomes available in that same session still follows the extra-usage policy normally. `airlock session-usage` and `airlock usage` do not cover OpenRouter, since it is a separate account with its own billing.

The reserved `auto` hybrid root never resolves to an OpenRouter route. It picks only among Fable, Opus, and Sonnet, so enabling OpenRouter cannot change what `auto` launches.

Read [How it works](how-it-works.md#openrouter-routes-optional) for the full explanation, including `airlock opr`, the 30-day metadata expiry, and the credential storage backends.

## Effort

The session starts at `AIRLOCK_MAIN_EFFORT`, which defaults to `high`. Change it for one session with `airlock --effort <level>`, or change it live at any point with Claude Code's `/effort` command.

Named `airlock-*` workers inherit the session level by default. That means `/effort` moves the main model and its workers together, including in the middle of a session. Nothing needs to be restarted.

The setup wizard presents three worker choices:

- **Follow session effort:** recommended; every unpinned worker moves with `/effort`.
- **Pin every worker:** all named workers keep one setup-time level.
- **Pin selected models:** chosen routes keep a level while the rest follow the session.

To change those values directly in the config, use:

```text
# every worker
AIRLOCK_WORKER_EFFORT=xhigh

# one worker, which wins over the setting above
AIRLOCK_EFFORT_LUNA=max
AIRLOCK_EFFORT_OPUS=xhigh
```

Use `inherit` to hand a worker back to `/effort`:

```text
AIRLOCK_WORKER_EFFORT=xhigh
AIRLOCK_EFFORT_LUNA=inherit
```

A pinned worker keeps its level no matter what `/effort` is set to. An inheriting worker follows the session.

Two limits are worth knowing:

- Claude Code's Agent tool currently has no per-call effort field. This applies to both Claude and OpenAI workers because Claude Code starts the Agent before Airlock routes the model request. The main model can choose a worker, but it cannot ask for a different effort for one task.
- If you ask for a level the active model does not support, Claude Code falls back to the highest supported level at or below it.

For now, use `/effort` when the session phase changes, `AIRLOCK_WORKER_EFFORT` for one default shared by all workers, or `AIRLOCK_EFFORT_<ROUTE>` for a model-specific default. Automatic task-specific effort routing is under design. It will remain documented as planned work until Airlock can implement it without falsely claiming that the native Agent call supports an effort value.

Airlock never sets `CLAUDE_CODE_EFFORT_LEVEL`. That variable overrides everything else, including `/effort`, so leaving it alone is what keeps the knob working.

### Effort on GPT roots

Claude Code decides whether a model supports effort by looking at the model ID. A pinned GPT ID matches none of its Anthropic patterns, so `/effort` would be missing on a GPT root. Airlock declares the supported levels for pinned GPT models to keep the knob available. Anthropic model IDs are left to Claude Code's own detection.

Change the declared levels with `AIRLOCK_GPT_EFFORT_CAPABILITIES` if you need to. The default is:

```text
AIRLOCK_GPT_EFFORT_CAPABILITIES=effort,xhigh_effort,max_effort
```

Anything left off that list is turned off for GPT roots, so remove entries only on purpose.

### Effort on Grok and OpenRouter roots

The same problem applies to any model ID that Claude Code does not recognize, so Grok roots and OpenRouter roots get the same treatment: every family slot the profile fills and the custom model option declare the effort capability tokens, and Airlock sets `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=1`. That switch tells Claude Code to show `/effort` even when the ID matches nothing it knows. An OpenRouter root therefore has a working effort knob without needing an entry in the capabilities variable, because the always-on switch covers IDs Airlock cannot predict.

If you export your own `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT`, your value wins.

## Auto mode classifier model

Claude Code runs a small permission classifier on tool calls that are not obviously safe, deciding whether to auto-approve them. That classifier ignores the session's model slots and hard-codes `claude-sonnet-5`. On a GPT, Grok, or OpenRouter root no route serves that exact ID, so every classified call failed closed and asked you to approve it by hand.

Airlock now seats the cheapest model the session serves in the undocumented `CLAUDE_CODE_AUTO_MODE_MODEL` knob on non-Anthropic roots. Classifications are constant background work, so they ride the small fast seat rather than the premium root: a hybrid session prefers Claude Haiku when one is enabled, a pure OpenAI session rides Luna, and a pure Grok session rides Composer. An OpenRouter root uses what its preset serves; a single-model preset has nothing smaller than its own root, which still beats failing closed. Anthropic roots keep stock behaviour with the knob cleared. You can override the choice:

```bash
# pin any exact model ID for the classifier
AIRLOCK_AUTO_MODE_MODEL=gpt-5.6-luna airlock hybrid sonnet

# turn the override off and go back to stock behaviour
AIRLOCK_AUTO_MODE_MODEL=off airlock opr
```

The same value works in the config file as `auto_mode_model`. Setting it to `off` or `none` clears the knob for that launch.

Note for Windows PowerShell users: explicit overrides travel through the exported environment variable. A value saved in the config file is applied by the bash launcher; the PowerShell path still gets the default policy, which picks the small seat automatically.

## OpenAI usage

```bash
airlock usage
airlock usage refresh
```

The command shows:

- percentage used and remaining
- window length and reset time
- age of the saved reading
- whether extra credits are available when the official response provides it

Airlock uses the documented Codex app-server `account/rateLimits/read` method. Bare `airlock usage` refreshes when the saved reading is missing or older than 15 minutes. A failed refresh keeps the last valid reading and labels it as cached.

Neither command sends a model request.

## Anthropic usage

Open native Claude Code and run:

```text
/usage
```

Anthropic shows personal subscription bars inside that screen. Anthropic does not document a safe noninteractive personal quota API that Airlock can read.

Airlock does not read login files, decode tokens, scrape the screen, or guess a percentage.

## Context window and auto-compaction

Claude Code decides when to compact from the context window assigned to the root process. Native Anthropic roots already have model-aware sizing, so Airlock leaves the process-wide override unset for them.

The authorized Sol proof above 300,000 tokens did not pass on 2026-08-09. Airlock therefore uses the honest bare ID `gpt-5.6-sol` and keeps the saved `AIRLOCK_CONTEXT_WINDOW` fallback for OpenAI roots and for Grok roots whose window is not documented. The fallback defaults to `272000`.

`grok-4.6` is documented at 500000 tokens. Airlock declares that hard limit with `CLAUDE_CODE_MAX_CONTEXT_TOKENS` and sets the compact threshold to 400000, so compaction still has room for the summary request.

| Root profile | Default behavior |
| --- | --- |
| Native Anthropic root | No process-wide override. Claude Code uses native model knowledge and `[1m]` where configured. |
| `grok-4.6` root | Declare 500000 and compact at 400000. |
| Other OpenAI or Grok root | Apply the saved `AIRLOCK_CONTEXT_WINDOW` fallback. |

Four rules go with it:

- A value you export yourself in `CLAUDE_CODE_AUTO_COMPACT_WINDOW` always wins.
- An explicitly exported numeric `AIRLOCK_CONTEXT_WINDOW` also wins, including on a `[1m]` root.
- `AIRLOCK_CONTEXT_WINDOW=auto` tells Airlock to set nothing and let Claude Code decide.
- The value has to be `auto` or a whole number from 100000 to 1000000. Claude Code silently ignores anything outside that range, so Airlock refuses it.

A larger explicit override means fewer, later compactions that summarize more history. It can also raise tokens per request once a session grows. Lower or remove the exported override when usage matters more than compaction frequency.

While the variable is set, Claude Code disables the auto-compact control in `/config`. The variable is process-wide, so the fallback or one explicit numeric value affects the root and every named worker. Airlock cannot safely give workers a separate threshold while retaining native Agents.

### Why long-context model IDs carry a `[1m]` suffix

Opus 5, Sonnet 5, and Fable 5 have a one million token context window. Claude Code grants it automatically, but only when `ANTHROPIC_BASE_URL` is unset or points at `api.anthropic.com`. Airlock always points that variable at its own session router, so Claude Code stops treating the connection as first party and drops all three models to 200000 tokens.

Nothing reports this. The session simply compacts four times as often as the same model would outside Airlock, which costs more usage rather than less.

Airlock fixes it by asking for those models as `claude-opus-5[1m]`, `claude-sonnet-5[1m]`, and `claude-fable-5[1m]`. Claude Code reads the suffix as a direct request for the one million token window, which it honors regardless of the base URL. The suffix never reaches Anthropic: Claude Code strips it and sends the base model name with the `context-1m-2025-08-07` beta header instead, so the router forwards an ordinary request. The router allowlist accepts both spellings for that reason.

GPT-5.6 Sol now uses the bare exact ID `gpt-5.6-sol`; Airlock does not label it `[1m]` after the failed proof. All OpenAI worker IDs, including Terra and Luna, are bare because no OpenAI route has a recorded successful proof above 300000 tokens. Legacy suffixed GPT input remains accepted at launcher and setup boundaries, then normalizes to the bare ID. The guarded helper in `scripts/test-sol-long-context.py` can test Sol separately with one synthetic root-only request when a new exact authorization is granted. On 2026-08-09 the installed candidate exited with status 1 and produced no valid marker or usage result, so Airlock retained the conservative OpenAI fallback.

Claude Haiku 4.5 is genuinely a 200000 token model, so it carries no suffix. Claiming a window a model does not have would let the session grow past what the API accepts and turn compaction into hard request failures.

Two limits still apply. The conservative OpenAI fallback, and the Grok 4.6 500000/400000 pair, also apply to every worker in that root process. Also, a one million token window is not available on every plan. A future release must pass the guarded proof before removing the OpenAI fallback.

## Native usage display

Because named workers are real Claude Code Agents, Claude Code owns their cards, state, cancellation, and display. Claude cards report native usage. Custom OpenAI and Grok IDs can still show zero on the native card even when the upstream response contains real usage fields.

Inside a hybrid session, run:

```bash
airlock session-usage
airlock session-usage --json
```

This reads only the active loopback router's sanitized cumulative summary. It shows per-provider/model request outcomes and provider-reported input, cache-write, cache-read, and output totals. It does not rewrite provider responses, spoof Claude model IDs, estimate missing values, or claim to be a bill. Provider-pure profiles have no router and fail clearly rather than guessing.

## Web search and page fetch

Claude Code's built-in WebSearch runs on Anthropic's API. In a session whose root is GPT, Grok, or an OpenRouter route, that tool cannot run at all. A pure profile also cannot run built-in WebFetch, because no Claude model is available for its Haiku family slot. Hybrid sessions keep both built-in tools working by seating Claude Haiku there.

For an OpenRouter route, the router also removes Anthropic server-tool declarations from the forwarded request. Without that, the upstream rejects the whole request with a 400 naming `web_search_20250305` before generating anything. The strip keeps the rest of the request working when a model tries WebSearch anyway; it does not execute searches, so use the local tools below instead.

Airlock gives each profile exactly one working web path:

| Root | Built-in WebSearch | Built-in WebFetch | Local airlock-web-tools |
|---|---|---|---|
| Hybrid with an Anthropic root | works | works | not registered |
| Hybrid with a GPT, Grok, or OpenRouter root | denied | works (seated Claude Haiku) | registered |
| Pure GPT, Grok, or OpenRouter root | denied | denied | registered |

Denied means the tool is refused through managed permissions before the model wastes a turn on a call that cannot succeed. The managed guidance paragraph tells non-Anthropic roots why the built-ins are gone and names the local tools instead.

The launcher hands the server to Claude Code through an `--mcp-config` file that is created at launch and deleted when the session ends, because Claude Code does not start `mcpServers` entries carried in `--settings`. The managed settings JSON keeps the same entry for forward compatibility.

The local server needs only Python 3 and ships inside the plugin. It exposes two tools:

- `web_search` takes `query` and optional `max_results` (1 to 12, default 6). It queries DuckDuckGo's HTML endpoint and returns ranked links with short descriptions. Follow up with `fetch_page` to read any result.
- `fetch_page` takes `url` and optional `max_chars`. It downloads one public page and returns readable text without sending it through any model.

Both tools contact the public web directly from your machine. They accept only http and https, resolve every address and refuse private, loopback, and link-local targets, follow redirects only while each hop passes the same checks, cap responses at 2 MB and 20 seconds, and truncate returned text to 20000 characters (up to 100000 when asked).

DuckDuckGo's HTML endpoint is not an official API. It behaves like a normal browser visit today, but DuckDuckGo can change the markup, add a bot challenge, or rate-limit heavy use without notice.

Set `AIRLOCK_WEB_TOOLS=off` before launching to omit the server entry, its guidance, and the denials for that session. An Anthropic-rooted session never sees any of this, because the built-in tools already work there.
