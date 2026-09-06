# Airlock Console

The console is the first visual surface for Airlock's session routers. It shows every Airlock session on this machine, what model each one is on, why one is blocked, what the router did about it, and which routes still have room. It is local-only software: it binds to 127.0.0.1, reads only what the routers already expose, and never renders prompts, responses, credentials, or provider error bodies.

This document is the contract between the server (`bin/airlock_console.py`), the routers (`bin/airlock-router.py`), and the page (`console/`). The JSON in `console/fixtures/` is the same contract as data. Both sides are tested against it.

## Principles

- One primary object: the session. Everything else (routes, cooldowns, headroom, timeline) is context for a session or for choosing a session's next model.
- Calm by default. Nothing animates or shouts unless a decision is needed from the person. One accent colour, reserved for that.
- Truthful about uncertainty. A value the router does not know renders as unknown, never as zero.
- Keyboard first, mouse complete. Every action reachable without a pointer; every state reachable with one.
- Light and dark from the first commit. Respect `prefers-color-scheme`, allow an override, persist it.
- No marketing surface. The console opens on the sessions list, not on a landing page.

## Architecture

```
airlock console            starts bin/airlock_console.py on 127.0.0.1:4783 and opens the browser
bin/airlock_console.py     stdlib HTTP server: static page, JSON API, SSE stream, session discovery
<runtime>/sessions/*.json  one registry file per live router, written by the router, removed on exit
router /diagnostics        per-session facts, polled by the console server about once a second
console/                   Vite + TypeScript page; built output committed under console/dist
```

Runtime root: on Windows `%LOCALAPPDATA%\Airlock\`, elsewhere `$XDG_STATE_HOME/airlock/` (default `~/.local/state/airlock/`). Routers already use `<root>/router-startups/`; the registry lives beside it in `<root>/sessions/`.

The console server is the only process that aggregates across sessions. Routers stay per-session and do not know about each other. The console never writes to a router.

## Router registry file

Written atomically by the router when it is ready, removed when it shuts down. Path `<root>/sessions/<instance_id>.json`, mode 0600 where the platform supports it.

```json
{
  "schema_version": 1,
  "instance_id": "r-8f2c1a",
  "url": "http://127.0.0.1:39123",
  "owner_pid": 41220,
  "router_pid": 41388,
  "started_at": "2026-09-05T08:41:03Z",
  "profile": "hybrid-anthropic-root",
  "root_model": "claude-fable-5-1[1m]",
  "root_provider": "anthropic",
  "workdir": "C:\\Users\\Harsh kamdar\\Desktop\\Opensource\\claudex"
}
```

The console treats an entry whose `owner_pid` is not alive, or whose URL does not answer `/healthz` with the `AirlockRouter` server header and the same `instance_id`, as ended, and removes the file. `workdir` is the directory the launcher started in; the launcher passes it to the router as an argument because the router runs with a scrubbed environment.

## Router diagnostics additions

`GET /diagnostics` keeps every existing field (`instance_id`, `profile`, `root_model`, `root_provider`, `rate_limit_cooldowns`, `rate_limit_provider_cooldowns`, `events`, `summary`) and adds:

```json
{
  "started_at": "2026-09-05T08:41:03Z",
  "owner_pid": 41220,
  "workdir": "C:\\Users\\Harsh kamdar\\Desktop\\Opensource\\claudex",
  "last_request_at": "2026-09-05T09:02:11Z",
  "pinned_model": null,
  "routes": [
    {"model": "claude-fable-5-1[1m]", "provider": "anthropic", "category": "metered",
     "context_window": 1000000, "effort_ceiling": "max", "metered": true}
  ],
  "cooldowns": [
    {"scope": "model", "model": "gpt-5.6-sol", "provider": "openai", "remaining_seconds": 1710},
    {"scope": "provider", "provider": "grok", "remaining_seconds": 240}
  ],
  "chains": {"gpt-5.6-sol": ["gpt-5.6-terra", "claude-opus-5[1m]"]},
  "context": {"model": "claude-fable-5-1[1m]", "input_tokens": 182340, "observed_at": "2026-09-05T09:02:11Z"}
}
```

`routes` comes from the validated snapshot, never from request content. `category` is one of `included`, `extra`, `metered`, `unknown`. `context.input_tokens` is the input side of the most recent completed request (input plus cache read plus cache creation), which is the best proxy the router has for conversation size. `chains` are the frozen per-model handoff orders the session launched with, declared or derived.

Events keep their current shape. The console renders only these event keys and ignores every other field: `timestamp`, `kind`, `model`, `provider`, `status`, `outcome`, `failover_from`, `to_model`, `from_model`, `models_considered`, `reason`, `remaining_seconds`, `usage` (numeric fields only), `duration_ms`.

## Console API

All responses are JSON unless stated. Every handler checks that `Origin`, when present, is a loopback origin, and answers 403 otherwise. No wildcard CORS. No caching headers that would let a shared proxy keep a response.

| Route | Returns |
| --- | --- |
| `GET /` and static | the built page from `console/dist` |
| `GET /api/overview` | `Overview` (below) |
| `GET /api/sessions` | `SessionSummary[]` |
| `GET /api/sessions/{id}` | `SessionDetail` |
| `GET /api/sessions/{id}/events?kind=&model=&since=` | `Event[]`, newest last, filtered |
| `GET /api/sessions/{id}/report.md` | Markdown incident report for the session |
| `GET /api/routes` | `RouteStatus[]` across all sessions |
| `GET /api/stream` | SSE. Event `overview` carries a full `Overview` whenever anything changed, at most twice a second. Event `heartbeat` every 15 s. |
| `GET /healthz` | `{ "ok": true, "sessions": n }` |

### Overview

```json
{
  "generated_at": "2026-09-05T09:02:12Z",
  "console_version": "0.1.0",
  "sessions": [ SessionSummary ],
  "routes": [ RouteStatus ],
  "headroom": [ ProviderHeadroom ],
  "attention": [ AttentionItem ]
}
```

### SessionSummary

```json
{
  "id": "r-8f2c1a",
  "state": "blocked",
  "blocked_reason": "rate_limit",
  "profile": "hybrid-anthropic-root",
  "root_model": "claude-fable-5-1[1m]",
  "root_provider": "anthropic",
  "active_model": "claude-opus-5[1m]",
  "workdir": "C:\\Users\\Harsh kamdar\\Desktop\\Opensource\\claudex",
  "project": "claudex",
  "started_at": "2026-09-05T08:41:03Z",
  "last_activity_at": "2026-09-05T09:02:11Z",
  "context": {"input_tokens": 182340, "window": 1000000},
  "workers": [{"model": "gpt-5.6-luna", "requests": 14}],
  "recent_handoffs": 1,
  "url": "http://127.0.0.1:39123"
}
```

`state` is one of:

- `running`: a request completed or started in the last five minutes and nothing is blocking the active model.
- `blocked`: the active model, or its provider, is in cooldown, or the newest event within the last ten minutes is a chain exhaustion or an unresolved context overflow. `blocked_reason` is `rate_limit`, `provider_cooldown`, `context_overflow`, or `chain_exhausted`.
- `idle`: alive, not blocked, no request in the last five minutes.
- `ended`: the owner process is gone. Shown for a short while so the person can still read the timeline, then dropped.

`active_model` is the pinned model when there is one, otherwise the root model. `project` is the last path segment of `workdir`. `workers` aggregates the usage summary for every model that is not the active model.

### SessionDetail

`SessionSummary` plus:

```json
{
  "routes": [ RouteStatus ],
  "cooldowns": [ Cooldown ],
  "chains": {"gpt-5.6-sol": ["gpt-5.6-terra", "claude-opus-5[1m]"]},
  "usage": [ {"provider": "openai", "model": "gpt-5.6-luna", "requests": 14, "completed": 14, "errors": 0,
              "input_tokens": 91200, "output_tokens": 8100, "cache_read_input_tokens": 60200} ],
  "events": [ Event ],
  "last_handoff": {"at": "2026-09-05T08:58:40Z", "from_model": "gpt-5.6-sol", "to_model": "gpt-5.6-terra", "reason": "rate_limit"}
}
```

### RouteStatus

```json
{
  "model": "gpt-5.6-sol",
  "short_name": "sol",
  "provider": "openai",
  "category": "included",
  "metered": false,
  "context_window": 400000,
  "effort_ceiling": "xhigh",
  "status": "cooling",
  "cooldown_remaining_seconds": 1710,
  "fits_context": false,
  "sessions_using": ["r-8f2c1a"]
}
```

`status` is `ready`, `cooling`, or `unavailable`. `fits_context` is only present inside a `SessionDetail`, relative to that session's context.

### Cooldown, ProviderHeadroom, AttentionItem, Event

```json
{"scope": "model", "model": "gpt-5.6-sol", "provider": "openai", "remaining_seconds": 1710, "until": "2026-09-05T09:30:41Z"}

{"provider": "anthropic", "window": "5h", "used_percent": 71, "resets_at": "2026-09-05T11:00:00Z", "source": "status_line"}
{"provider": "grok", "window": "5h", "used_percent": null, "resets_at": null, "source": "unknown"}

{"kind": "session_blocked", "session_id": "r-8f2c1a", "since": "2026-09-05T08:58:40Z", "summary": "Opus is rate limited; chain exhausted"}

{"timestamp": "2026-09-05T08:58:40Z", "kind": "rate_limit_failover_attempted", "model": "gpt-5.6-terra", "provider": "openai", "failover_from": "gpt-5.6-sol", "models_considered": 2}
```

Plan headroom is phase 1b. Until a source exists, every provider reports `source: "unknown"` and the page renders that honestly. The page is designed with the slot from the start.

## Event kinds the page must name in plain words

`rate_limit_failover_attempted`, `rate_limit_failover_succeeded`, `rate_limit_cooldown_skipped`, `rate_limit_provider_cooldown`, `rate_limit_chain_exhausted`, `failover_overflow_attempted`, `failover_overflow_succeeded`, `failover_overflow_skipped`, `failover_shrink_compacted`, `failover_shrink_truncated`, `failover_shrink_failed`, `overflow_chain_exhausted`, `upstream_context_overflow`, `openrouter_effort_clamped`, `openrouter_server_tools_stripped`, `sanitized_error_substituted`, `background_model_substituted`, `anthropic_rate_limit_passthrough`, `model_not_enabled`, `session_root_selected`, `session_model_pinned`, `session_model_unpinned`, `router_restarted`, and plain request events (a `status` and `outcome` with no `kind`). Unknown kinds render with their raw name, never hidden.

## Phase 1 page

Three regions, one page, no routing beyond a session id in the URL hash.

- **Sessions rail (left).** Every session, grouped by state with blocked first. Each row: project name, active model short name, state, time since last activity, a slim context bar. Arrow keys move, Enter opens, `/` filters. Empty state explains how to start an Airlock session and that the console will notice it.
- **Session view (main).** Header: project, working directory, active model with provider, state with reason, context used against the window, uptime. Below it the timeline: newest first, grouped by minute, each entry a plain sentence ("Sol was rate limited, handed off to Terra after considering 2 routes"), filterable by kind group (handoffs, overflow, cooldowns, requests, other) and by model. A handoff is drawn as from to to. Export report copies Markdown.
- **Inspector (right).** Routes for this session: ready first, then cooling with a countdown, then unavailable; each with provider, tier, window, whether it fits this session's context, metered mark. Chains for the active model. Provider headroom with unknowns shown as unknown. Workers spawned with request counts.
- **Attention strip (top, only when non-empty).** One line per blocked session. Clicking selects it.

Command palette on Ctrl+K: jump to session, filter timeline, toggle theme, copy report. `?` lists shortcuts.

Live updates through SSE; the page reconnects with backoff and shows a quiet "reconnecting" state, never a modal.

## Phase 2 and 3, for shape only

Phase 2 adds proposals, approvals with revision-bound tokens, resume, the chain editor, mode switches, and alerts. Phase 3 adds the loopback MCP server, the HTTP tool API, WebMCP in the page, and non-Airlock sources. The phase 1 data model must not need renaming to grow into these: proposals attach to a session id, tokens attach to a proposal revision, external sessions are `SessionSummary` rows with `source: "claude-code" | "generic"`.

## Build and serve

- `console/` holds `package.json`, `vite.config.ts`, `src/`, `fixtures/`, `dist/`. Vite plus TypeScript. Preact is allowed; heavy UI kits, CSS frameworks, and icon fonts are not. Inline SVG icons only.
- `npm run dev` serves the page against `fixtures/` through a mock of the API and SSE stream, so the page is developable with no router running. `?fixture=<name>` selects a fixture.
- `npm run build` writes `dist/`; `dist/` is committed; `npm run check:dist` fails when `dist/` does not match a fresh build. Tests in `console/tests/` run with Vitest.
- `bin/airlock_console.py` serves `console/dist` resolved relative to the installed Airlock tree, and the installer copies `console/dist` beside `bin/`.
- Bundle size budget for the page: under 150 kB gzipped total.

## Security constraints

- Bind 127.0.0.1 only. Refuse to start if the port is in use by anything that is not an Airlock console.
- Loopback `Origin` check on every API and SSE request. No credentials of any kind in the console server, ever.
- Render only allowlisted fields from routers and registry files. Treat registry files as untrusted input: validate schema, ignore unknown keys, cap sizes.
- No prompt or response text, no provider error bodies, no tokens, no account identifiers, anywhere in the console or its API.
- The console never restarts, signals, or writes to a router or a session in phase 1.
