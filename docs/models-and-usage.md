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

Model access depends on your account and can change. A model supported by the proxy may still be unavailable on your plan.

## Advanced: background command and utility model

These settings are not part of normal Agent routing.

- `airlock bg` is a separate convenience command with its own saved OpenAI model and starting effort. Normal named Agents do not run through it.
- The utility model handles lightweight Claude Code requests such as titles. The setup wizard chooses an economical default, and most users do not need to change it.

Both controls stay under Advanced in the setup wizard because they are optional implementation details, not choices required for a first session.

## Built-in Agent model choice

Plan and general-purpose inherit the orchestrator model when `model` is omitted. Routine Explore uses the exact economical discovery model named in the generated guidance. If an unpinned Explore would inherit a different premium root, the guard blocks it and names the exact enabled retry model.

A main model can give Explore, Plan, or general-purpose any exact full model ID enabled for the active session. Explicit exact choices always win. The guard still rejects aliases, disabled routes, unknown IDs, and extra-usage models without confirmation.

Named `airlock-*` Agents already have an exact model. They follow the session effort unless setup pins a level. Callers cannot override either value for one Agent call.

Claude Code may omit GPT IDs from `/model` discovery behind a gateway. Starting the exact root with `airlock` or `airlock hybrid`, using a named Agent, or passing an exact allowed Agent model ID is more reliable than depending on discovery.

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

A number saves a smaller cap for new sessions. Top-level spawn depth remains one and named Agents cannot invoke Agent. This keeps fan-out visible at the root.

## Provider Fast controls

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

Luna Fast uses `gpt-5.6-luna-fast[1m]`. It can consume plan capacity faster. API pricing changes do not guarantee more subscription quota.

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

The authorized Sol proof above 300,000 tokens did not pass on 2026-08-09. Airlock therefore uses the honest bare ID `gpt-5.6-sol` and keeps the saved `AIRLOCK_CONTEXT_WINDOW` fallback for OpenAI and Grok roots. The fallback defaults to `272000`.

| Root profile | Default behavior |
| --- | --- |
| Native Anthropic root | No process-wide override. Claude Code uses native model knowledge and `[1m]` where configured. |
| OpenAI or Grok root | Apply the saved `AIRLOCK_CONTEXT_WINDOW` fallback. |

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

GPT-5.6 Sol now uses the bare exact ID `gpt-5.6-sol`; Airlock does not label it `[1m]` after the failed proof. Terra and Luna retain their existing exact `[1m]` IDs for Claude Code routing compatibility, but those suffixes are not treated as proof of usable provider context. The guarded helper in `scripts/test-sol-long-context.py` can test Sol separately with one synthetic root-only request when a new exact authorization is granted. On 2026-08-09 the installed candidate exited with status 1 and produced no valid marker or usage result, so Airlock retained the conservative OpenAI fallback.

Claude Haiku 4.5 is genuinely a 200000 token model, so it carries no suffix. Claiming a window a model does not have would let the session grow past what the API accepts and turn compaction into hard request failures.

Two limits still apply. The conservative OpenAI or Grok fallback also lowers every worker in that root process. Also, a one million token window is not available on every plan. A future release must pass the guarded proof before removing the fallback.

## Native usage display

Because named workers are real Claude Code Agents, Claude Code owns their cards, state, cancellation, and display. Claude cards report native usage. Custom OpenAI and Grok IDs can still show zero on the native card even when the upstream response contains real usage fields.

Inside a hybrid session, run:

```bash
airlock session-usage
airlock session-usage --json
```

This reads only the active loopback router's sanitized cumulative summary. It shows per-provider/model request outcomes and provider-reported input, cache-write, cache-read, and output totals. It does not rewrite provider responses, spoof Claude model IDs, estimate missing values, or claim to be a bill. Provider-pure profiles have no router and fail clearly rather than guessing.
