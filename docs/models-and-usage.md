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
```

`airlock hybrid` uses the saved hybrid root. `airlock hybrid choose` opens the full picker. An explicit OpenAI alias such as `airlock terra` stays OpenAI-only even when bare `airlock` is saved as hybrid.

Model access depends on your account and can change. A model supported by the proxy may still be unavailable on your plan.

## Advanced: background command and utility model

These settings are not part of normal Agent routing.

- `airlock bg` is a separate convenience command with its own saved OpenAI model and starting effort. Normal named Agents do not run through it.
- The utility model handles lightweight Claude Code requests such as titles. The setup wizard chooses an economical default, and most users do not need to change it.

Both controls stay under Advanced in the setup wizard because they are optional implementation details, not choices required for a first session.

## Built-in Agent model choice

Explore, Plan, and general-purpose inherit the orchestrator model by default.

A main model can give one of those built-ins an exact full model ID for one call. The guard allows it only when the route is enabled for the current session.

The OpenAI-only profile allows enabled OpenAI IDs. Hybrid allows enabled OpenAI and Anthropic IDs.

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
- Budget selects economy, blocks extra usage, and disables automatic failover.
- Defaults restores the tested settings.

## Top-level concurrency

```bash
airlock mode max-agents off
airlock mode max-agents 3
```

`off` is the default. It removes the Airlock cap and uses Claude Code's native limit. It is not permission to start unnecessary workers.

A number saves a smaller cap for new sessions. Top-level spawn depth remains one and named Agents cannot invoke Agent. This keeps fan-out visible at the root.

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

- `auto` selects Luna Fast only when the sanitized OpenAI plan is exactly `prolite` or `pro` and proxy support is verified.
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

- Claude Code has no per-call effort field on the Agent tool. The main model can choose which worker to use, but it cannot ask for a different effort for one task. Only you can change effort, with `/effort` or by pinning.
- If you ask for a level the active model does not support, Claude Code falls back to the highest supported level at or below it.

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

## Native usage display

Because named workers are real Claude Code Agents, Claude Code owns their cards, state, cancellation, and usage display. Airlock no longer needs a transport wrapper to estimate worker tokens in normal sessions.

Native counters are still not a provider bill or proof of remaining plan quota.
