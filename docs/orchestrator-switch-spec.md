# Orchestrator handoff architecture

Status: proposal, version 3. Supersedes version 2 of this file, which
superseded the first draft.
Written: 2026-08-16. Amended 2026-08-19, section 13, the implementation
contracts. Against Airlock 0.1.0-beta.4, bundle 2026.08.15.1, Claude Code
2.1.x.

Every claim about Claude Code below comes from the official documentation or from
this machine. Every claim about Airlock comes from the code, with file and line
references. Items that could not be verified without spending plan quota are
listed in section 16 and are not assumed anywhere else.

## 1. The short answer

Yes, this is possible. Four of the five pieces you asked for are solvable with
good engineering. One piece has a hard floor that no amount of engineering
removes, and the design has to be built around it rather than pretending it is
not there.

| What you asked for | Verdict |
| --- | --- |
| Detect when a provider is rate limited | Solved. Three independent signals exist, two of which Airlock does not use yet. |
| Choose the next provider | Solved. Deterministic policy engine, no model call needed. |
| Manage state across the handoff | Mostly free. Claude Code restores more than expected on resume, and the rest fits in one small state file. |
| Manage context on handoff | Solved, with real algebra behind it. Section 6. |
| Compact when the context is over the next provider's limit | Solved for the common cases, with one case that cannot keep full fidelity. Section 7. |
| Do it all with zero human input | Not possible without killing the session process. Section 2. |

**The hard floor.** Claude Code reads the base URL, the model, the context
window, the agent set, the settings, and the system prompt once, at process
start. Changing the orchestrator changes most of those. The documentation is
explicit that no hook can terminate or restart a session. So a real provider
change always crosses a process boundary, and the only things that can end that
process are the user or Airlock's own launcher, which is the parent process.

That gives two honest options and no third one:

- The user presses exit, or types one command. One keystroke, clean.
- Airlock's launcher sends the running session a termination signal and relaunches
  it. Fully automatic, and it interrupts whatever the session was doing.

Everything else in this document works the same under either choice.

**Complexity.** This is a subsystem, not a feature. Roughly 3,000 to 4,500 lines
of production code and 1,500 to 2,500 lines of tests, spread across twelve files
and both platforms, in five phases. The first phase that is genuinely useful on
its own is about a fifth of that. Full breakdown in section 12.

## 2. What Claude Code actually allows

These are the load bearing facts. All are documented.

| Fact | Consequence for this design |
| --- | --- |
| No hook can terminate or restart a session. `SessionEnd` fires on the way out and cannot prevent or trigger the exit. | The process boundary needs the user or the parent launcher. |
| `SessionEnd` reasons are `clear`, `resume`, `logout`, `prompt_input_exit`, `bypass_permissions_disabled`, `other`. | Airlock's current handoff accepts only `prompt_input_exit`. A launcher-driven exit would arrive as `other`, so the marker rules have to widen deliberately, not by accident. |
| A `StopFailure` hook fires when a turn ends from an API error, with matchers including `rate_limit`, `overloaded`, `billing_error`, `authentication_failed`, `model_not_found`, `server_error`. | This is the live rate limit detector, delivered by Claude Code itself, for whatever provider the router just talked to. |
| `StopFailure` ignores hook output except `terminalSequence`. | The hook can record state and notify the terminal. It cannot speak to the model or block anything. |
| The status line command receives JSON on stdin with `rate_limits.five_hour.used_percentage`, `rate_limits.seven_day.used_percentage`, both `resets_at` values, `context_window.used_percentage`, `context_window.current_usage`, `exceeds_200k_tokens`, `session_id`, `transcript_path`, `model`, `effort.level`. | This is a documented, live, structured read of Anthropic plan usage and of current context occupancy. Airlock currently states that no such read exists. |
| `--resume <id>` keeps the same session ID. `--fork-session` creates a new one. `--session-id <uuid>` sets one. | Multi hop handoffs work, and a seeded restart can pick its own ID. |
| Resume restores conversation, model unless a flag or env var overrides it, agent, permission mode with exceptions, active goal, and unexpired scheduled tasks. It does not restore background Bash or monitor tasks, and does not restore `--settings`, `--plugin-dir`, `--mcp-config`, `--add-dir`, or `--fallback-model`. | Airlock passes all of those on every launch already, so the relaunch is clean. Background work is the one real casualty. |
| `/compact` is terminal only. It does not work in `-p` mode. `/model`, `/effort`, `/config`, `/mcp`, `/color` do work in `-p`. | There is no headless compaction. This shapes the whole compaction plan in section 7. |
| `/autocompact <value>` saves the value to the user's own settings file. | Airlock must never run it. Airlock uses the `--autocompact` flag, which is per launch and does not touch user settings. |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` outranks the flag, the command, and the setting. | Airlock's existing precedence handling stays correct. |
| `CLAUDE_CODE_MAX_CONTEXT_TOKENS` declares the window for a model ID Claude Code does not recognize, and applies directly when the ID neither starts with `claude-` nor contains `[1m]`. | This is the correct way to tell Claude Code the truth about `gpt-5.6-sol`, `grok-4.6`, and OpenRouter IDs. Airlock now uses it for `grok-4.6`. |
| For an unrecognized ID containing `[1m]`, Claude Code assumes 1M and the variable only applies alongside `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`, which is process wide and would also cap real Claude models at 200K. | Airlock's earlier decision to drop the `[1m]` suffix from GPT IDs is what keeps the clean case available. Keep it that way. |
| For an ID that resolves to a Claude model, `CLAUDE_CODE_MAX_CONTEXT_TOKENS` only applies with `DISABLE_COMPACT`, which turns compaction off entirely. | Never use it for Claude roots. |
| When the context exceeds the model's limit mid conversation, Claude Code compacts automatically and continues. | A downward switch is survivable, within limits. |
| `CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1` makes that recovery wait for the API's too long error, and the recovery does not run when a gateway rewrites the error. | Airlock is a gateway. Whether the recovery survives the Codex proxy's error translation is verification item V3. |
| Compaction fails with `Error during compaction: Conversation too long` when the conversation nearly fills the window, with no retry. | Compaction is itself a request that must fit. This is the single most important constraint in section 6. |
| Session and weekly plan limits are shared across models, so switching models does not help. A model specific limit such as the Opus limit, and a 529 capacity error, are per model, so switching models does help. | The selection engine must treat these two failure classes differently. Section 8. |
| `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` populates the `/model` picker from the gateway's `/v1/models` endpoint. | Airlock's router already serves `/v1/models` with every enabled route, at `bin/airlock-router.py:443`. This makes in process switching real. |
| `availableModels` restricts which models can be selected. | Gives the picker a fail closed allowlist that matches the route policy. |
| A `-p` run stopped with SIGTERM aborts the turn, runs `SessionEnd` hooks, and exits 143. | The launcher driven exit has documented behavior in headless mode. Interactive behavior is verification item V2. |

## 3. What Airlock already has

The handoff machinery exists. It is currently pointed at one fixed target.

| Piece | Location |
| --- | --- |
| Marker create, arm, finalize, consume, cleanup, with nonce, launcher PID, cwd, session binding, TTL, atomic writes, 0600 | `bin/airlock-access.py:2691` to `bin/airlock-access.py:2851` |
| Arm command exposed as `airlock fast --arm` | `bin/airlock:1441`, `bin/airlock.ps1:1362` |
| SessionEnd finalizer | `plugins/airlock/scripts/fast-session-end.py` |
| Consume and relaunch with `--resume` | `bin/airlock:1249` to `bin/airlock:1277` |
| Windows consume and relaunch | `bin/airlock-hybrid.py:960` to `bin/airlock-hybrid.py:1000` |
| One hop limit | `bin/airlock:1172`, `bin/airlock-hybrid.py:995` |
| Full per launch re derivation of agents, guidance, settings, snapshot, router | `bin/airlock:1035` to `bin/airlock:1160` |
| Router that routes any enabled model ID by exact match, and serves `/v1/models` and `/diagnostics` | `bin/airlock-router.py:434` to `bin/airlock-router.py:490` |
| Per request provider, model, status, outcome records | `bin/airlock-router.py:510` |
| OpenAI plan buckets with used percent and reset time, no model request | `airlock usage`, stored in `access.json` |
| Route capability and cost labels for every model | `bin/airlock-access.py:204` |
| Failover policy with ask, never, allow | `AIRLOCK_FAILOVER_POLICY` |

So the work is not "build a handoff". It is "make the handoff general, correct
about context, driven by real signals, and safe to repeat".

## 4. Two modes, not one

There are two different ways to change the orchestrator, with different costs.
The architecture should ship both, because neither one covers every case.

### Mode A, in process switch

Stay in the same process. Change the model with `/model`. The router already
routes every enabled ID to the right provider, so the request lands correctly the
moment the model changes.

What blocks this today, and what fixes each block:

| Block | Fix |
| --- | --- |
| The picker may not list cross provider IDs | Set `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`. The router's `/v1/models` already returns the exact enabled set. Pair it with `availableModels` so the list is also the allowlist. |
| The context window is process wide and fixed at launch | Launch switch ready sessions with the window set to the smallest hard limit in the switch set, and the compaction threshold below that. Conservative, but correct in every direction. |
| The generated guidance names one root | Write the guidance for switch ready sessions in provider neutral form: it already lists every enabled worker, so the root sentence becomes a statement about the current root that the model re reads from `AIRLOCK_ROOT_MODEL` rather than a fixed claim. |
| The worker pool is fixed at launch | Not a problem. A hybrid session already carries every enabled provider's workers. |
| Profile shape cannot change | Real limit. A pure OpenAI session cannot become hybrid in process. Mode A only works inside a hybrid session. |

Cost of Mode A: the whole session runs at the smallest window in the set. If the
set includes a 272,000 route, a Claude root in that session compacts at 272,000
instead of 1,000,000. For your usage pattern that is a meaningful loss, so this
must be opt in per session, not a default.

Benefit: no restart, no lost background work, no lost todos, no marker, no
lifecycle. One command. This is the right answer for "Opus limit reached, move to
Sonnet" and for "GPT weekly window is out, move to Claude for an hour".

### Mode B, handoff relaunch

Exit and relaunch with `--resume`, re deriving everything. This is the general
mechanism and the only one that can:

- change the profile shape, for example OpenAI only to hybrid
- give the new root its own correct window instead of a shared conservative one
- land on an `airlock opr` root
- change the agent set, guidance, and router configuration

Cost: a restart, the loss of in flight background work, and a cold prompt cache.

### How they relate

Same detection, same selection, same context fit logic. The planner picks the
mode:

```
if session is hybrid and target is in the session's switch set and window fits:
    Mode A, print the exact /model command
else:
    Mode B, arm a handoff
```

## 5. The five subsystems

```
                   +---------------------+
                   |  1. Signals          |
                   |  StopFailure hook    |
                   |  status line JSON    |
                   |  airlock usage       |
                   |  router diagnostics  |
                   +----------+-----------+
                              |
                   +----------v-----------+
                   |  2. Health model      |
                   |  per provider state   |
                   |  bounded, session     |
                   |  scoped, no secrets   |
                   +----------+-----------+
                              |
             +----------------+----------------+
             |                                 |
  +----------v-----------+        +------------v-----------+
  |  3. Selection engine  |        |  4. Context planner     |
  |  which route next     |<------>|  does it fit, and if    |
  |  and why              |        |  not, how to shrink it  |
  +----------+-----------+        +------------+-----------+
             |                                 |
             +----------------+----------------+
                              |
                   +----------v-----------+
                   |  5. Transition        |
                   |  Mode A instruction   |
                   |  or Mode B marker,    |
                   |  exit, relaunch,      |
                   |  state carry over     |
                   +----------------------+
```

Each is independently testable. Three of the five need no model request at all.

## 6. Context management, the algebra

This is the part that decides whether a handoff is safe, so it needs exact
definitions rather than a vague "does it fit".

```
H  hard limit        what the provider's API will actually accept in one request
W  declared window   what Claude Code believes the model has
T  compact threshold how full the conversation gets before Claude Code compacts
O  occupancy         tokens the conversation currently carries
```

### 6.1 The compaction headroom invariant

Compaction is a model request that carries the conversation it is summarizing.
So compaction can only succeed while the conversation still fits in one request.

```
T  <  H  -  (summary instruction + summary output budget)
```

If `T` equals `H`, the trigger fires exactly when the request no longer fits, and
the documented result is `Error during compaction: Conversation too long`, with
no retry and no recovery except `/clear`.

**This matters for Airlock today, before any handoff work.** Airlock sets
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` to `AIRLOCK_CONTEXT_WINDOW`, which defaults to
272,000, for OpenAI roots and undocumented Grok roots (`bin/airlock:750`).
272,000 was chosen as the conservative belief about the real limit. So those
thresholds currently sit at the believed hard limit, with no headroom. If the
true limit is 272,000, a long GPT session compacts for the first time at the
exact point compaction can no longer run. `grok-4.6` now declares 500,000 and
compacts at 400,000. The same small fix still applies to the remaining roots:

```
CLAUDE_CODE_MAX_CONTEXT_TOKENS = H          # tell the truth about the model
--autocompact                  = 0.8 * H    # leave room for the summary request
```

`CLAUDE_CODE_MAX_CONTEXT_TOKENS` applies directly for IDs that neither start with
`claude-` nor contain `[1m]`, which is exactly the shape of every non Claude ID
Airlock uses. Claude roots keep native sizing and must not receive the variable.

### 6.2 The window table, corrected

| Root | H, hard limit | W to declare | T to set |
| --- | --- | --- | --- |
| `claude-opus-5[1m]`, `claude-sonnet-5[1m]`, `claude-fable-5[1m]` | 1,000,000 | leave native | leave native, or `0.8 * H` when the session is switch ready |
| `claude-haiku-4-5-20251001` | 200,000 | leave native | leave native |
| `gpt-5.6-*` | believed 272,000, unproven above 300,000 | 272,000 | 218,000 |
| `grok-4.6` | documented 500,000 | 500,000 via `CLAUDE_CODE_MAX_CONTEXT_TOKENS` | 400,000 |
| `grok-4.5`, `grok-composer-2.5-fast` | unknown | 272,000 as the conservative fallback | 218,000 |
| OpenRouter route | the pinned endpoint's context length | that value | `0.8 *` that value |

The OpenRouter numbers are per endpoint, not per model, and Airlock does not
store them today. Measured from the public catalog on 2026-08-16 for your three
declared routes:

| Route | Pinned endpoint | Endpoint context |
| --- | --- | --- |
| `kimi-k3` | `digitalocean` | 1,048,576 |
| `qwen-3-6-27b` | `chutes/fp8` | 262,144 |
| `deepseek-v4-flash-0731` | `deepinfra/fp4` | not in the catalog today; DeepInfra now serves `deepinfra/fp8` at 1,048,576 |

Two consequences. The registry must freeze `context_length` alongside the
provider slug and quantization, or an OpenRouter route cannot be a handoff
target. And that DeepSeek route is already drifting, so a refresh is due
regardless of this work.

Also note what an `airlock opr` root gets today: no window declaration at all, on
either platform (`bin/airlock:865`, `bin/airlock-hybrid.py:519`). Claude Code
falls back to whatever it assumes for an unrecognized ID. That is a gap this work
closes.

### 6.3 Measuring occupancy

Two sources, in order of preference:

1. **The status line JSON.** `context_window.current_usage` and
   `context_window.used_percentage` come from the last API response, live, and
   are a documented interface.
2. **The transcript tail.** The last assistant entry's `message.usage` summed as
   `input + cache_read + cache_creation + output`. On this session that reads
   148,338 tokens. It works, but the documentation states plainly that the
   transcript format is internal and changes between versions, so this is the
   fallback, not the primary.

Preferring the status line has a cost: Airlock has to own the `statusLine`
setting for the session, which would displace a status line the user configured
themselves. So it is opt in, and when the user already has one, Airlock's wrapper
must run theirs and print its output unchanged, adding nothing of its own unless
asked. If the user declines, the transcript reader carries on alone.

### 6.4 The fit decision

Let `H_t` be the target's hard limit and `O` the current occupancy.

| Condition | Verdict | Action |
| --- | --- | --- |
| `O <= 0.8 * H_t` | fits | switch, nothing else needed |
| `0.8 * H_t < O <= H_t` | tight | switch works, but the new root compacts almost immediately. Better to compact first on the outgoing root, where the model that holds the context writes the summary. |
| `O > H_t` | does not fit | must shrink before landing. Section 7. |
| occupancy unknown | unknown | say so, do not assume it fits |

## 7. The compaction planner

The requirement "if the context is over the next provider's limit then compact
it" runs straight into one fact: `/compact` is terminal only. There is no
headless compaction, so Airlock cannot silently compact a transcript between two
launches. The planner works with what does exist.

Four paths, in preference order.

**Path A, compact before leaving.** The outgoing session runs `/compact` with
focus instructions while it is still able to answer, then arms the handoff. Best
quality, because the model that lived through the conversation writes the
summary. Requires the outgoing provider to still serve one large request.

This is the strongest argument for proactive switching. A weekly limit that is
already exhausted cannot compact anything. A weekly limit at 85 percent can.
Detection that fires at a threshold, not at failure, is what keeps Path A
available.

**Path B, land and let Claude Code compact.** Relaunch on the target with `W` set
truthfully and `T` at `0.8 * H_t`. If the conversation exceeds the limit mid
conversation, Claude Code compacts automatically and continues. This works while
`O <= H_t`, because the compaction request itself has to fit. It also depends on
the too long error surviving the proxy's translation, which is verification item
V3.

**Path C, shrink through a wide provider.** When `O` is larger than the intended
target's limit but smaller than some other enabled route's limit, hand off to the
wide route first, compact there, then hand off again to the intended target. Two
hops. A Claude 1M route or a 1M OpenRouter endpoint is the natural intermediate.
This is the reason the hop budget in section 9 is more than one.

**Path D, seeded restart.** When nothing fits, fidelity has to be given up. Start
a new session with its own ID and seed it with:

- a mechanical digest Airlock can build with no model call: changed files from
  `git status` and `git diff --stat`, the active todos, the branch, the handoff
  chain and reason
- the last assistant brief, if the outgoing model was able to write one
- the last few user messages verbatim, subject to a size cap

The seed is injected through the `SessionStart` hook's `additionalContext`, which
is capped at 10,000 characters, so the seed is bounded by construction. The old
transcript stays on disk and remains resumable, so nothing is destroyed.

Selection between the paths is deterministic:

```
if O <= 0.8 * H_t                      -> no compaction needed
elif outgoing provider can still answer -> Path A
elif O <= H_t                           -> Path B
elif exists route R with H_R >= O       -> Path C through R
else                                    -> Path D
```

One honest note that belongs in the user facing docs: compaction is lossy, and a
handoff plus a compaction is two losses at once. The new root gets a summary
written under time pressure by a model that is being replaced. Switching early,
while the outgoing provider still has capacity, produces a much better handoff
than switching at zero.

## 8. Detection and selection

### 8.1 Signals

| Signal | Where it comes from | Covers | Proactive or reactive |
| --- | --- | --- | --- |
| `StopFailure` hook | Claude Code, in session | every provider the router talks to | reactive, fires on the failure |
| Status line JSON `rate_limits` | Claude Code, in session | Anthropic plan windows, five hour and seven day, with reset times | proactive |
| `airlock usage` buckets | Codex app server, no model request | OpenAI plan windows with reset times | proactive |
| Router `/diagnostics` | Airlock's own router | any provider, including worker traffic the root never sees | reactive, and the only one that sees worker failures |

The status line is the discovery that changes the design. Airlock's docs
currently say Anthropic exposes no readable plan window. That is true of any
API Airlock could call itself, and it stays true. It is not true of Claude Code's
own status line contract, which hands a local script the five hour and seven day
percentages and their reset times. Airlock can read its own session's plan state
without touching a credential file, without an undocumented endpoint, and
without a model request.

Grok stays unreadable. OpenRouter stays out of scope, consistent with
`airlock session-usage` already omitting it.

### 8.2 The health model

One bounded, session scoped, 0600 JSON file, schema validated, no credentials, no
prompts, no transcript text:

```json
{
  "schema_version": 1,
  "updated_at": 0,
  "providers": {
    "anthropic": {
      "authenticated": true,
      "five_hour_used_percent": 41.0,
      "seven_day_used_percent": 88.0,
      "resets_at": 0,
      "recent_failures": [{"kind": "rate_limit", "at": 0, "model": "claude-opus-5"}],
      "cooldown_until": 0,
      "scope": "account"
    }
  }
}
```

`scope` is the important field. It records whether the last failure was account
wide or model specific, because the two have opposite remedies.

### 8.3 The selection engine

A pure function. No model call, fully unit testable, deterministic tie breaking.

```
select(current_route, failure, health, policy, occupancy, catalog) -> ranked candidates
```

1. Start from the enabled routes for this session's profile.
2. Drop routes that are unauthenticated, disabled, in cooldown, or gated behind an
   extra usage confirmation the user has not given.
3. Apply the failure scope:
   - model scoped failure, which means an Opus style per model limit or a 529
     capacity error, keeps the provider and moves to a sibling model. Cheapest
     possible move, no credential change, no profile change.
   - account scoped failure, which means a session or weekly limit, removes the
     whole provider until `resets_at`.
4. Split the survivors into those with `H >= O` and those without. Prefer the
   first group. If it is empty, mark the plan as needing Path C or Path D.
5. Rank what is left by task fit first, using the capability labels Airlock
   already carries at `bin/airlock-access.py:204`, then by relative cost under the
   active routing preference, then by window headroom.
6. Never silently cross into paid extra usage. Never pick a route whose login or
   proxy is unhealthy.
7. Break remaining ties in a fixed documented order so tests stay stable.

The output is a ranked list with a one line reason for each entry, which is what
`airlock switch --check` prints and what the automatic path consumes.

Worth stating plainly, because it is counterintuitive: within Anthropic, a
session or weekly limit is shared across all models, so moving from Opus to
Sonnet does nothing. Only a model specific limit is fixed by staying on the same
provider. The engine has to know the difference or it will recommend moves that
cannot work.

### 8.4 Worker routes are a separate case

If a worker's provider is rate limited while the root is healthy, no handoff is
needed. The right response is to stop selecting that route.

The Agent guard already runs before every `Agent` call. It reads the router
summary, and when a route has repeated recent 429s it denies the call with a
schema valid retry naming an enabled alternative, in the same style as the
existing unpinned Explore guard. Cooldowns are session scoped and expire. This
works only in router backed profiles, which is a limitation worth stating rather
than hiding.

## 9. State across the handoff

### 9.1 What Claude Code carries

Documented as restored on resume: the conversation with tool calls and results,
the model unless a flag or environment variable overrides it, the agent, the
permission mode with `plan` and `bypassPermissions` deliberately excluded, an
active goal with its counters reset, and unexpired scheduled tasks.

Documented as not restored: background Bash and monitor tasks, and the launch
flags `--settings`, `--plugin-dir`, `--mcp-config`, `--add-dir`,
`--fallback-model`. Airlock reconstructs every one of those on each launch, so
the only genuine casualty is background work.

Not carried by anything: the prompt cache. The first turn after a handoff
reprocesses the whole conversation on the new provider. On a large session that
is a large request, which is exactly the resource you were short of. Another
argument for switching early.

### 9.2 What Airlock has to carry

One chain file, bounded and validated, holding:

- a chain ID and the hop number
- for each hop: from route, to route, reason, timestamp, mode A or B, whether a
  compaction happened
- the health snapshot at the moment of the switch, so the new session does not
  immediately recommend the provider that just failed
- active route cooldowns
- the seed digest pointer, for Path D only

Not carried: extra usage confirmations, which must be given again, and anything
resembling a credential, a prompt, or transcript content.

### 9.3 Telling the new session what happened

The `SessionStart` hook with matcher `resume` returns two things:

- `systemMessage`, for the user: one line naming the old root, the new root, the
  reason, and whether a compaction happened.
- `additionalContext`, for the model: the same facts plus the brief, capped and
  clearly labelled as a record of the previous session rather than as an
  instruction.

This is a deliberate change of stance. Airlock's existing update notice hook
avoids `additionalContext` on purpose so that notices never enter the model's
context. Here the model genuinely needs to know it changed providers mid task, so
the field is used, bounded, and labelled.

## 10. Automation tiers

Ship them in this order. Each is a superset of the one before.

| Tier | Behavior | Human input | Default |
| --- | --- | --- | --- |
| 0, manual | `airlock switch <target>`, exit, relaunch | picks target, exits | available |
| 1, assisted | Airlock detects, names the best target and the reason, one command arms it | confirms, exits | recommended default |
| 2, armed | On a rate limit failure Airlock arms the handoff itself and prints one line. The relaunch happens on the next clean exit | exits when convenient | opt in |
| 3, supervised | The launcher signals the running session to stop and relaunches it | none | opt in, off by default |

Tier 3 is where "fully automatic" lives, and it is the only tier that can
interrupt work in progress. Its risks are concrete: a turn is aborted mid tool
call, background work dies, and a user who was typing loses what they typed. It
should require an explicit setting, should never fire while a tool call is
running, and should be limited to sessions that opted in for long unattended
runs. In headless mode the behavior is documented, the turn aborts and
`SessionEnd` hooks run. In interactive mode it needs verification, item V2.

Tier 1 is the one to build first and the one most users should stay on. When a
provider is exhausted the session cannot do anything anyway, so waiting for the
user costs nothing real.

## 11. What this changes in existing Airlock

| Area | Change |
| --- | --- |
| Context window handling | Move from `CLAUDE_CODE_AUTO_COMPACT_WINDOW` alone to `CLAUDE_CODE_MAX_CONTEXT_TOKENS` for the declared window plus `--autocompact` for the threshold, with the headroom invariant. Give `opr` roots a declared window. |
| OpenRouter registry | Freeze the endpoint context length. Schema version bump, refresh required, fail closed for entries that lack it. |
| Router | Count 429s per provider and model, capture numeric `retry-after`, expose both in the existing bounded summary. Optionally set gateway model discovery so `/v1/models` drives the picker. |
| Plugin | Two new hooks: `StopFailure` for the limit watcher, `SessionStart` matcher `resume` for the handoff notice. Optional `statusLine` wrapper when the user opts in. |
| Marker | Version 2, carrying a validated route token and profile rather than a fixed model, plus hop count and kind. The launcher recomputes the model from the catalog, so a tampered marker cannot introduce a model ID. |
| Launchers | Channel per hop instead of one, hop budget, target resolution, revalidation before relaunch, and the seeded restart path. Both `bin/airlock` and `bin/airlock-hybrid.py`. |
| Guidance | A handoff section: only the user can trigger one, write the brief before exiting, do not claim a switch happened, stop selecting a route that is cooling down. |
| Threat model | New surface: the marker carries a route token, the launcher can be told to relaunch repeatedly, Airlock reads plan percentages and context occupancy from a Claude Code contract, and a bounded prior session brief enters model context. |
| Docs claim about Anthropic usage | Currently "Anthropic does not document a safe noninteractive personal quota API that Airlock can read". Still true about APIs. Needs a sentence about the status line contract, which is a different thing. |

Two latent issues surfaced by this research, both independent of the feature:

1. The compaction threshold for OpenAI and Grok roots sits at the believed hard
   limit with no headroom, which is the configuration in which compaction fails
   rather than saves the session. Section 6.1.
2. An `airlock opr` root gets no window declaration at all, so Claude Code
   compacts at whatever it assumes for an unrecognized ID.

## 12. Complexity

Sized by component, at this repository's quality bar, including tests and docs.

| # | Component | Production | Tests | Risk |
| --- | --- | --- | --- | --- |
| 1 | Window correctness: declared window, threshold headroom, per route table, `opr` roots, registry context length | 250 to 400 | 250 | medium, needs V3 |
| 2 | Occupancy: status line wrapper plus transcript fallback | 150 to 250 | 200 | low |
| 3 | Detection: `StopFailure` watcher, health file, router counters, usage buckets | 400 to 600 | 350 | medium, needs V1 |
| 4 | Selection engine | 250 to 400 | 300 | low |
| 5 | Marker v2, hop budget, both launchers, target resolution and revalidation | 700 to 1000 | 500 | medium |
| 6 | Context planner and the four compaction paths | 300 to 500 | 250 | high |
| 7 | Seeded restart, Path D | 300 to 450 | 250 | medium |
| 8 | Mode A switch ready sessions: model discovery, allowlist, shared window, neutral guidance | 200 to 350 | 200 | low |
| 9 | Tier 3 supervised auto exit | 200 to 300 | 200 | high |
| 10 | Guidance, docs, threat model, bundle manifest, changelog | 150 | 100 | low |
| | **Total** | **2,900 to 4,400** | **2,600** | |

Phases, each shippable and each useful on its own:

| Phase | Contents | Why stop here is safe |
| --- | --- | --- |
| 1 | Components 1, 2, 4 and a read only `airlock switch --check` | Fixes the window bugs, measures occupancy, tells you where to go. Changes no session behavior. |
| 2 | Component 5 plus 6's Path A and B, Tier 0 and 1 | The feature you asked for, manual and assisted, with correct context handling. |
| 3 | Component 3 and 8 | Real detection and the cheap in process switch. |
| 4 | Components 7 and 6's Path C and D | Handles the cases where the context does not fit anywhere. |
| 5 | Component 9 | Full automation for unattended runs. |

Phase 1 is roughly a fifth of the work and is worth doing whatever happens to the
rest, because two of its items are current bugs.

The honest calendar view: phases 1 and 2 are the bulk of the user visible value
and are perhaps a week of focused work plus live verification. All five phases
with the same test discipline the rest of this repository has is closer to three
to four weeks, and the long pole is verification, not code.

## 13. Implementation contracts

Four contracts that pin down the data shapes and conflict rules the phases
in section 12 build against. They are normative. An implementation that
deviates from one of them is a deviation from this spec. They add precision
to sections 7 through 9 and change no behavior those sections define.

### 13.1 Checkpoint schema and freshness metadata

The checkpoint is a per session JSON file in the session runtime directory,
0600, schema validated, bounded to 64 KB and LRU pruned inside the bound. It
never contains a credential, a prompt, or transcript text beyond the bounded
`critical_turns` field, and it references the old transcript by path and
session ID only. It never modifies it.

The file has two kinds of sections with different freshness semantics, and a
reader must never treat the file as uniformly fresh. Mechanical sections are
written by hook events alone, cost no model request, and carry a capture
timestamp. Semantic sections are written by a model request, cost tokens, and
carry their own freshness and provenance per section.

```json
{
  "schema_version": 1,
  "session_id": "...",
  "created_at": 0,
  "updated_at": 0,
  "source_turn": 0,
  "repo_revision": "abc123",
  "degraded": false,
  "pending_refresh": false,
  "mechanical": {
    "git": {
      "branch": "feat/x", "status": "...", "diff_stat": "...",
      "commits": ["..."],
      "captured_at": 0, "event": "stop"
    },
    "files": {
      "touched": ["..."], "read": ["..."],
      "captured_at": 0, "event": "stop"
    },
    "todos": { "open": ["..."], "captured_at": 0 },
    "agents": [
      { "name": "research", "state": "uncertain",
        "snapshot_ref": "worker-snapshots/research-1755570000.json",
        "failed_at": 0 }
    ],
    "routes": { "root": "openai/sol", "cooldowns": { "anthropic": 0 },
                "captured_at": 0 },
    "usage": { "bucket": "7d", "used_percent": 88.0, "resets_at": 0,
               "captured_at": 0 }
  },
  "semantic": {
    "objective": {
      "text": "...",
      "refreshed_at": 0, "trigger": "milestone", "turn": 141,
      "model": "claude-opus-5", "repo_revision": "abc123", "tokens": 512
    },
    "critical_turns": {
      "text": "...", "turn_from": 138, "turn_to": 141,
      "refreshed_at": 0
    }
  }
}
```

`acceptance`, `plan`, `decisions`, and `blockers` share the `objective`
shape. `critical_turns` is the last few user and assistant turns verbatim,
bounded to 8,192 characters. The mechanical `agents` section holds the
lifecycle state only. The 13.4 snapshot is a separate bounded file that it
points at.

Rules:

1. Every mechanical section carries `captured_at` and the hook event that
   wrote it, one of `user_prompt`, `stop`, `session_end`. Its freshness is
   that timestamp, full stop.
2. Every semantic section carries `refreshed_at`, the `trigger` that produced
   it, the source `turn`, the authoring `model`, and the `repo_revision` at
   write time. Provenance is per section, because a refresh may rewrite some
   sections and leave others untouched. A section that was not rewritten
   keeps its old provenance, which tells the reader exactly which parts are
   still from an older turn.
3. Staleness is computed at read time, per section. A semantic section is
   stale when its `repo_revision` differs from the file's current
   `repo_revision`, or when the mechanical git section shows a HEAD or file
   move after its `refreshed_at`. Any consumer that injects a stale section,
   the handoff notice or a worker snapshot, must label it as a record as of
   its `turn` and `repo_revision`, never present it as current, and must not
   let a stale section block a handoff. Staleness is a quality marker, not a
   gate.
4. `degraded` is true when the last refresh ran while the provider was
   unhealthy, so the semantic sections are the last healthy ones and no
   newer semantic state exists. `pending_refresh` is true when a 13.2
   trigger fired inside the spacing window and a refresh is owed.
5. An unknown `schema_version` fails closed. The reader ignores the file,
   notices it, and falls back to the seeded restart view. It never crashes
   on a newer or older schema.

This supersedes section 9.2's seed digest pointer, for Path D only. The
Path D seed is the injection view of this file. The bounded digest plus a
pointer to the full file on disk, which the new root can read.

### 13.2 Semantic refresh rule

There is no per hour volume cap on semantic refreshes. The cost control is a
minimum spacing floor plus meaningful change triggers, because an intense
thirty minute session can change direction completely, and an hourly cap
would leave the checkpoint stale through exactly that window.

Triggers. Any one of them makes a refresh owed:

1. The repo revision moved since the last semantic refresh.
2. A milestone. A commit was made, the test suite result flipped, or the
   user ran `airlock checkpoint refresh`. The manual trigger always runs,
   even inside the spacing window, because the user is asking for it and the
   cost is known.
3. Occupancy crossed a configured target's compact threshold. The checkpoint
   must be fresh before a handoff plan, and this is the trigger that makes
   it so proactively.
4. A handoff is being armed. If the outgoing provider is still alive, the
   arm flow runs one refresh first. If it is not alive, the arm proceeds
   with the current sections and sets the stale labels. A dead provider
   never blocks a handoff.

Spacing. At most one non manual refresh per `AIRLOCK_CHECKPOINT_SPACING`,
default 15 minutes, configurable between 5 and 60. Triggers that fire inside
the window coalesce into a single refresh at the next open slot and set
`pending_refresh` until then. An intense thirty minute session with three
meaningful changes therefore gets one refresh at the right moment, not three
and not zero.

Skip conditions, which set `degraded` instead of refreshing. The provider
is unhealthy, so a refresh would spend a request on the dying provider. Or
`AIRLOCK_CHECKPOINT_SEMANTIC=off`, which is mechanical only mode and the
checkpoint carries no semantic sections at all.

A refresh writes `refreshed_at`, `turn`, and `repo_revision` only to the
sections it actually rewrites.

### 13.3 Health store conflict semantics

The health entries of section 8.2 are keyed by provider and credential
fingerprint, each entry scoped `account` or `route`, and each carrying
`category`, `retry_at`, `confidence`, `high` or `medium` or `low`, `source`,
`recorded_at`, `recorded_by`, and `contested`. There is no event log.
Conflict resolution is a deterministic precedence over the current entry:

1. Fresh success outranks current evidence at its scope. A recorded success
   on a route within the success window, default 10 minutes, clears that
   route's cooldown entries. A success on one of an account's routes clears
   account scope entries of medium and low confidence.
2. Documented exception. A high confidence account entry whose `retry_at`
   came from the usage API is not cleared by a success while `now` is
   before `retry_at`. A small success cannot disprove a reset that has not
   happened yet, metering lags, and clearing it would let other sessions
   slam the same account and rediscover the same exhaustion, which is the
   exact failure this store exists to stop. The entry is marked `contested`
   instead. Any later failure re arms it immediately, with no backoff
   ladder, and a success after `retry_at` clears it normally.
3. Failure evidence. Higher confidence wins. High outranks medium outranks
   low. A low confidence 429 never displaces a current high confidence
   account exhaustion. It is dropped, not logged.
4. Same confidence. Fresher `recorded_at` wins. Ties break on the larger
   `retry_at`, then on `recorded_by`, for determinism across processes.
5. Scope is preserved. Route evidence mutates route entries only, and
   account evidence mutates account entries only. An account entry excludes
   all of that provider's routes at selection time, and that exclusion is
   computed at read time, never stored as route entries.
6. `retry_at` only extends across processes, by the existing max rule,
   except where rule 1 or rule 2 clears the entry.

Concurrency is the existing atomic write plus compare and swap on
`updated_at`, and on a conflict the writer re reads and merges entry by entry
with the rules above, never whole file last writer wins. The store stays
bounded, 0600, and secret free. Evidence is a source name and non secret
values, never an error body or a credential.

### 13.4 Worker snapshot contract

When a worker call fails mid task, the guard and the session health writer
build a bounded snapshot with no model request. It is the unit that a fresh
retry carries. A running subagent cannot be re rooted, so a retry is always
a new worker on the fallback model, and the snapshot is what lets it skip
the work the dead attempt already did.

```json
{
  "schema_version": 1,
  "worker": "research",
  "role": "codebase research, no edits",
  "task": "...",
  "primary": "anthropic/sonnet",
  "substitute": "openai/sol",
  "files_touched": ["..."],
  "files_read": ["..."],
  "completed_steps": ["..."],
  "partial_result": "...",
  "partial_artifact_path": "...",
  "failure": {
    "category": "account_quota_exhausted",
    "scope": "account",
    "retry_at": 0
  },
  "repo_revision": "abc123",
  "failed_at": 0,
  "substitution_count": 1
}
```

Bounds. `task` 4,096 characters, `files_touched` 32 entries, `files_read`
64, `completed_steps` 20 entries of 200 characters, `partial_result` 8,192
characters, whole snapshot 16 KB. `failure` carries classifier fields only,
never a raw error body. `completed_steps` and the file lists come from the
subagent's own transcript tail and tool records, read bounded.

The fresh retry's input is the original task plus a labelled snapshot block
named previous attempt. Substitute model, failure category, scope, and
retry time, completed steps, partial result, files, and repo revision. The
assembly is deterministic, no model judgment goes into it, and the fresh
worker may skip completed steps.

Budget. `substitution_count` caps at 2 per task, enforced by the guard
reading the snapshot. A third failure stops with a one line reason. Depth is
1. A substituted subagent does not substitute its own subagents. Snapshots
land in the checkpoint's `agents` section by reference, bounded to the 8
most recent, and they are what `airlock switch --check` reports for worker
state.

## 16. Verification items

These cannot be settled from documentation. Each needs an authorized live test
naming provider, model, files, and worker count, per the repository rule.

| ID | Question | Why it matters | If the answer is no |
| --- | --- | --- | --- |
| V1 | Does `StopFailure` classify a 429 as `rate_limit` when it arrives through the router from the Codex proxy or from Anthropic? | It is the whole live detector | Fall back to router diagnostics, which sees the raw status |
| V2 | Does an interactive session run `SessionEnd` on SIGTERM, and does the transcript stay resumable? | Tier 3 depends on it | Tier 3 is dropped, tiers 0 to 2 are unaffected |
| V3 | Does Claude Code's automatic overflow compaction survive the Codex proxy's error translation? | Path B depends on it | Path B is removed and the planner leans on A, C, D |
| V4 | Does a transcript written under Claude replay correctly through the proxy, and does proxy shaped history replay correctly back to Anthropic? Thinking blocks, signatures, tool use, images. | The whole of Mode B | Restrict supported directions, or force Path D across those pairs |
| V5 | What is the real hard limit of the GPT routes through the proxy? | Sets `H`, and therefore the threshold | Keep 272,000 as the conservative floor |
| V6 | Do the status line `rate_limits` fields populate for a session whose base URL is the router? | Proactive Anthropic detection | Detection stays reactive for Anthropic |
| V7 | Does `--resume` keep the same session ID under our launcher, so a second hop can arm again? | Multi hop | Read the current ID at arm time from `CLAUDE_SESSION_ID`, which the skill already does |

V4 is the one that can change the shape of the design, so it should be tested
first, before any code is written for phase 2. It is cheap to test: a short
session on Claude, a handoff to Sol, one turn, a handoff back.

## 17. Decisions I need from you

1. **Mode A default.** Should switch ready sessions be opt in per launch, for
   example `airlock hybrid sol --switch-ready`, or a saved mode? Opt in per
   launch is my recommendation, because the cost is a smaller window for the
   whole session.
2. **Status line.** Airlock owning `statusLine` is the clean way to read plan
   percentages and occupancy. Is that acceptable when it means wrapping or
   displacing a status line you configured yourself?
3. **How far up the tiers to go.** My recommendation is to build through tier 2
   and treat tier 3 as a separate decision after V2 is answered.
4. **Compaction stance.** When the context does not fit, should Airlock refuse
   and tell you to compact, or offer Path C automatically through a wide
   provider, which spends usage on a route you did not pick?
5. **Seeded restart.** Is losing the transcript acceptable as a last resort, or
   should Airlock refuse the handoff instead and leave you to decide?
6. **The window bug in section 6.1.** Fix it now as a standalone change, or fold
   it into phase 1 of this work?

## 18. What this will never do

Worth writing down so the feature does not overpromise.

- It will not switch providers without a process boundary and therefore without
  either your keystroke or a deliberate opt in to being interrupted.
- It will not make a rate limit disappear. It moves work to a different account
  that you already pay for.
- It will not hide the cost. Every handoff pays a full context request on arrival
  because the prompt cache does not survive.
- It will not compact silently in the background, because non interactive
  compaction does not exist.
- It will not guarantee that a conversation which grew past every available
  provider's window keeps full fidelity. That case loses detail by definition.
- It will not read credential files, invent a quota number, or change provider
  behind your back.
