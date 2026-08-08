# How Airlock works

Airlock is a launch and policy layer around Claude Code. Claude Code still owns the conversation, terminal, permissions, tools, Agent cards, background tasks, cancellation, and worktree lifecycle.

## Saved launch profiles

```bash
airlock                 # saved default profile
airlock openai          # saved OpenAI-only root
airlock grok            # saved Grok-only root
airlock hybrid          # saved hybrid root
```

The setup wizard writes `AIRLOCK_DEFAULT_PROFILE` and saves one root for each profile. New setups recommend hybrid with GPT-5.6 Sol. Existing configs without the profile key retain the original OpenAI-only bare command.

The OpenAI-only profile points Claude Code directly at the local `claude-code-proxy` endpoint on `127.0.0.1`. It starts with an enabled OpenAI model and exposes only enabled OpenAI Agent models. No mixed-provider router is needed.

An explicit OpenAI alias such as `airlock terra` always starts this profile. `airlock hybrid MODEL` always starts the mixed-provider profile.

The Grok-only profile works the same way. It points Claude Code at the same local proxy, which selects the Grok upstream from the model ID and its own Grok login. Codex and Grok never share a login even though they share the proxy.

## Hybrid routing

```bash
airlock hybrid sol
airlock hybrid opus
```

A hybrid session starts one temporary router on an unused `127.0.0.1` port. Claude Code uses that one address for the whole session.

The router reads only the top-level model ID needed for routing:

- exact enabled `gpt-*` IDs go to the local OpenAI proxy
- Claude Code's deterministic wire form also routes to the same provider, such as `gpt-5.6-sol[1m]` becoming `gpt-5.6-sol` or `claude-opus-5[1m]` becoming `claude-opus-5`
- exact enabled `claude-*` IDs go to `https://api.anthropic.com`
- exact enabled `grok-*` IDs go to the same local proxy as GPT, which picks the Grok upstream
- unknown or disabled IDs fail closed

The router registers a wire form only for an enabled full model ID. It does not accept arbitrary aliases. The request body is forwarded without rewriting it. Streaming responses are sent to Claude Code as they arrive.

On an Anthropic route, the router preserves Claude Code capability and Agent attribution headers. It forwards saved Claude login authorization opaquely to Anthropic.

On an OpenAI route, it removes incoming Claude authorization, API-key, cookie, proxy authorization, and OAuth capability headers before calling the local proxy. Claude credentials are never sent to OpenAI.

The router binds to loopback, has a parent-process lifetime, rejects redirects, and does not log prompts, response bodies, or credentials. Its local `/diagnostics` endpoint exposes only a bounded in-memory list of provider, exact enabled model, status, byte-count, duration, outcome, and token-count metadata for parity checks. Token counts are read from the response that was already forwarded, so they never change, delay, or buffer the stream. When an upstream sends no usage at all, the event simply has no token counts rather than a made-up zero.

## Native Agents

Every enabled named worker is a real Claude Code Agent.

Examples:

```text
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
```

Each name has:

- one exact full model ID
- the session effort level, unless you pin one
- a direct technical prompt
- Claude Code's normal subagent tool pool
- native background execution and cancellation
- native Agent cards and usage
- optional native worktree isolation

Named workers cannot invoke Agent and a caller cannot override their model. This keeps the card name, model, and role consistent. Their effort follows the session unless the config pins it.

A worker runs inside Claude Code itself, not inside a shell command. A long response cannot be lost to a shell timeout.

## Built-in Explore, Plan, and general-purpose

Airlock keeps the exact built-in Agent types:

- Explore for read-only repository discovery
- Plan for read-only technical design
- general-purpose for multi-step work

By default, they inherit the orchestrator model. This includes Explore.

For one call, the main model may pass an exact full model ID:

```text
Agent(subagent_type="Explore", model="gpt-5.6-luna[1m]", ...)
Agent(subagent_type="Plan", model="claude-opus-5[1m]", ...)
```

The session guard checks the ID before Claude Code starts the Agent.

- The OpenAI-only profile allows only enabled OpenAI IDs.
- The Grok-only profile allows only enabled Grok IDs.
- Hybrid allows enabled OpenAI and Anthropic IDs, plus Grok IDs when Grok is enabled.
- Aliases, `inherit`, malformed IDs, disabled models, blocked extra-usage routes, and ineligible Fast routes are rejected when supplied as overrides.
- Omitting `model` keeps normal inheritance.

A named Agent has no per-call effort field. It follows the session effort by default, and `/effort` can move it in the middle of a session. A configured pin stays fixed until the config changes.

## Model selection and `/model`

A provider-pure profile cannot leave Claude Code's Fable, Opus, Sonnet, or Haiku slots pointing at native Claude IDs. Selecting one would send the wrong provider's model ID to the subscription proxy. Airlock fills all four slots from models that are enabled for that provider:

- OpenAI Fable and Opus use Sol, Sonnet uses Terra, and Haiku uses Luna or eligible Luna Fast. A missing route falls back to the closest enabled OpenAI model.
- Grok Fable and Opus use Grok 4.5, while Sonnet and Haiku use Composer. A missing route falls back to the enabled Grok model.
- A route that still needs explicit extra-usage confirmation is not placed in `/model`, because the picker has no way to carry Airlock's confirmation marker.
- The exact root named on the launch command remains available as the custom option, even when it is not one of those worker routes.

These are Claude Code picker slots, not claims that GPT-5.6 Sol is Claude Opus or that Composer is Claude Haiku. Airlock sets each label to the exact provider model ID so the menu reports what will actually receive the request. It also clears every proxy-specific slot before a hybrid session, where the Claude family names keep their native meanings.

The hybrid router can route both providers because every model uses the same local endpoint. This does not guarantee that every GPT ID appears in Claude Code's `/model` menu. Claude Code gateway discovery can ignore non-Claude IDs.

Use these reliable paths:

- start the saved root with `airlock`, the OpenAI-only root with `airlock openai`, or an exact root with `airlock terra` or `airlock hybrid MODEL`
- use an exact named `airlock-*` Agent
- pass an allowed exact model ID to Explore, Plan, or general-purpose

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

A stronger Sol, Opus, Grok, or capable main model reviews, integrates, tests, and synthesizes the full result. Sol, Terra, Opus, Sonnet, Fable, Haiku, and Grok 4.5 are not multiplied automatically.

A session is only told about the routes it actually enabled. A Grok-only session is not given Luna army instructions, and a session with no economical high-volume route is told plainly that it has no automatic swarm route.

## Concurrency

```bash
airlock mode max-agents off
airlock mode max-agents 3
```

`off` removes the Airlock cap and uses Claude Code's native limit. It does not mean unlimited work and it is not a reason to start unnecessary Agents.

A number saves a smaller cap for new sessions.

Top-level Agent spawn depth is one. Named workers also disallow the Agent tool. Fan-out stays with the main model, which prevents hidden Agent trees and keeps usage visible.

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

## Usage and failure handling

`airlock usage` reads OpenAI plan windows through the documented Codex app-server method. It does not send a model request.

Grok has no equivalent readable plan window, so Airlock reports Grok headroom as unknown rather than guessing. It does record whether the proxy holds a Grok login, and a session that confirms it is signed out disables the Grok routes instead of advertising workers that would fail on their first request.

Use native Claude Code's `/usage` screen for Anthropic subscription bars. Airlock does not read Claude login files or guess Anthropic percentages.

An explicit provider or model never silently changes. The router returns upstream status and error bodies to Claude Code so native retries can work, but an unknown route fails locally.

## Managed bundle checks

The installer writes the managed bundle marker last. The launcher checks hashes for launchers, helpers, Agent catalogs, and the session plugin before starting.

```bash
airlock bundle
```

A stale, changed, missing, or incomplete managed file stops startup and asks for a reinstall and fresh session.

## Support boundary

Anthropic documents Claude Code gateways and subscription login forwarding. Anthropic does not officially support non-Claude models behind those gateways. Airlock is an independent compatibility layer, so provider or Claude Code updates can affect mixed routing.

The project keeps protocol-level tests for the router, the guards, and the worktree hooks, and it checks live behavior separately before each release. See [Testing](testing.md) and [Live tests](live-tests.md).
