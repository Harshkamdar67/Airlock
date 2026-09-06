# Airlock Console control and agent contract

This document extends `SPEC.md` for product phases 2 and 3. Phase 1 is read-only. This contract adds live-session pinning, human-reviewed proposals, global chain editing, stdio MCP, plain HTTP tools, and WebMCP.

The hackathon Relay protocol is not carried forward. Airlock Console does not launch a resumed terminal, rewrite the global failover chain to hand off one session, expose approval tokens to agents, or make a person click Approve and then wait for an agent to execute. Approval applies the decision immediately to the already-running session router.

## Threat boundary

Approval requires a person to review and press Approve in Airlock Console. This separates normal agent tool access from the approval path. It is not a security boundary against software running with the person's local account privileges. Such software can read local files, invoke the CLI, or automate the browser.

Practical separation still matters:

- Agent-facing MCP, HTTP tool, and WebMCP surfaces can read and propose. They have no approve, reject, execute, pin, unpin, resume, or raw router-control operation.
- Browser mutations require exact same-origin requests and a per-console CSRF token unavailable through JSON APIs, tool responses, reports, or events.
- Router controls require a per-router token stored only in its private registry file and private console state.
- The control token and CSRF token never appear in diagnostics, console JSON, page state, logs, events, reports, MCP, or WebMCP.

## Session pinning

A handoff pins the live session router to a target route for future requests. It does not restart Claude Code. The existing conversation, process, worktree, and task remain unchanged.

Each request:

1. Validate the model Claude Code requested. Unknown models remain rejected even when a pin exists.
2. Resolve the existing configured route or valid background-model substitution.
3. Snapshot `pinned_model` under a short routing lock.
4. If pinned, retarget every valid request to the pin, including background and compaction requests.
5. If unpinned, keep current direct and background-substitution behavior.
6. Use the effective pinned model as the source of its frozen failover chain. A temporary automatic failover does not change the pin.
7. Unpinning restores normal root and background behavior for later requests.

A request already resolving when the pin changes may use its previous snapshot. No lock is held across upstream network I/O.

A pin is rejected when its model or provider is cooling, or when the observed conversation size and target window are both known and the conversation is too large. Unknown context or window is allowed but reported as `context_check: "unknown"`. Unpin is always allowed.

Startup records `session_root_selected`. A real pin records `session_model_pinned`. Root restoration records `session_model_unpinned`. These events carry only `timestamp`, `kind`, `model`, and `provider` where applicable.

## Private router registry and control

Registry schema version 2 adds one private field to the schema in `SPEC.md`:

```json
{
  "schema_version": 2,
  "control_token": "opaque per-router random token"
}
```

The token is at least 256 random bits, stored only in the mode-0600 registry file. Console accepts schema version 1 for read-only compatibility and disables controls for that row.

Router endpoints remain loopback-only. Both require `Content-Type: application/json`, an `X-Airlock-Control-Token` header compared in constant time, a loopback Origin when Origin is present, a small bounded body, and exact object keys.

| Endpoint | Body | Effect |
| --- | --- | --- |
| `POST /control/pin` | `{"model":"exact frozen model id"}` | Pin future requests to that route. |
| `POST /control/unpin` | `{}` | Restore normal root routing for future requests. |

Success:

```json
{
  "ok": true,
  "action": "pin",
  "instance_id": "8f2c1a",
  "changed": true,
  "previous_pinned_model": null,
  "pinned_model": "claude-opus-5[1m]",
  "route": {
    "model": "claude-opus-5[1m]",
    "provider": "anthropic",
    "context_window": 1000000,
    "context_check": "fits"
  }
}
```

Pinning the same target returns 200 with `changed: false` and the current pin.

Unpin is exact and idempotent:

- A real unpin returns `changed: true`, `previous_pinned_model` naming the removed model, `pinned_model: null`, and `route: null`. Only this transition records `session_model_unpinned`, naming the removed model.
- An already-unpinned router returns 200 with `changed: false`, `previous_pinned_model: null`, `pinned_model: null`, and `route: null`, and records nothing.
- A retry after a lost response converges on the same final state instead of failing.

Context observations come only from foreground requests: the session root or the effective pin. A request that directly names the configured background model, or one substituted to it, never replaces a larger foreground observation, so a later pin cannot appear to fit a conversation that does not.

| Status | Safe error type |
| --- | --- |
| 400 | `invalid_request` or `model_not_enabled` |
| 403 | `forbidden` for bad token or Origin |
| 404 | `not_found` |
| 409 | `route_cooling` or `context_too_large` |
| 413 | `request_too_large` |

The router never accepts chain edits, process operations, arbitrary configuration, or a generic action endpoint.

## Proposals

Proposal IDs are the kind prefix plus at least 256 random bits. Server-owned revisions start at 1. Creation and editing set an expiry fifteen minutes ahead. Pending proposals expire in the poll loop and on mutations. Completed, rejected, expired, and failed proposals remain visible for one hour, under a fixed count cap. Console restart deliberately forgets all proposals and never recovers or retries an action.

Only pending, failed, or conflicted proposals can be edited. Edits require `expected_revision`, increment the revision, clear the safe error, and reset expiry. One active handoff proposal per session and one active chain proposal are enough. A new proposal never silently replaces an active one.

| Status | Safe error type |
| --- | --- |
| 409 | `stale_revision` |
| 409 | `chain_conflict` |
| 409 | `application_in_progress` |
| 409 | `route_state_changed` |
| 409 | `active_proposal_exists` |
| 409 | `not_routed` |

`not_routed` answers a proposal against a plain Claude Code session. Those sessions have no Airlock router, so they have no routes to move between; the console lists them read-only from their transcript's tail (session id, working directory, last timestamp, last reply's model) and nothing else.

Approval has no token. It atomically moves pending to applying, revalidates against current facts, performs the action synchronously, and records applied, failed, or conflicted. Repeated approval of applied returns the recorded result with `already_applied: true`. A per-proposal lock prevents two applications.

### SessionHandoffProposal

```json
{
  "id": "shp_...",
  "kind": "session_handoff",
  "session_id": "8f2c1a",
  "operation": "pin",
  "target_model": "claude-opus-5[1m]",
  "reason": "The active provider is cooling and Opus fits the conversation.",
  "allow_metered": false,
  "created_by": "agent",
  "created_at": "2026-09-05T09:10:00Z",
  "expires_at": "2026-09-05T09:25:00Z",
  "revision": 1,
  "status": "pending",
  "basis": {
    "observed_at": "2026-09-05T09:10:00Z",
    "active_model": "claude-fable-5-1[1m]",
    "pinned_model": null,
    "context_input_tokens": 612340,
    "route_status": "ready"
  },
  "application": null,
  "last_error": null
}
```

`operation` is `pin` or `restore_root`. Restore-root has a null target. Status is `pending`, `applying`, `applied`, `rejected`, `expired`, `superseded`, `conflicted`, or `failed`. Application records attempts and safe timestamps, router instance ID, previous and resulting pin, changed, and already_applied. It carries no secret.

### ChainChangeProposal

```json
{
  "id": "ccp_...",
  "kind": "chain_change",
  "chains": {"gpt-5.6-sol":["claude-opus-5[1m]","grok-4.6"]},
  "base_digest": "sha256 of canonical logical current chains",
  "reason": "Prefer another frontier provider before a metered route.",
  "created_by": "agent",
  "created_at": "2026-09-05T09:10:00Z",
  "expires_at": "2026-09-05T09:25:00Z",
  "revision": 1,
  "status": "pending",
  "application": null,
  "last_error": null
}
```

Application performs a digest compare-and-swap. A changed file produces conflicted and is never overwritten. Global chain copy always says: "Applies to sessions started after this change. Running sessions keep their frozen chains."

## Global chain storage

The CLI and Console share one implementation in the access helper:

1. Validate through existing failover validation.
2. Acquire a private cross-platform advisory lock beside failover.json.
3. Reload current logical chains.
4. Compare canonical SHA-256 digest when the caller supplies an expected digest.
5. Write canonical JSON to a same-directory mode-0600 temporary file.
6. Flush and fsync, then `os.replace`; fsync the directory on POSIX.
7. Remove failover.json atomically for an empty map.

Existing sessions keep the chains frozen in their launch snapshot.

## Console browser API

A per-process CSRF token is injected only while serving the root HTML. It is not returned by any JSON endpoint. Every mutation requires exact same-origin Origin, JSON content type, and `X-Airlock-CSRF` compared in constant time.

| Endpoint | Channel | CSRF |
| --- | --- | --- |
| existing overview, sessions, routes, events, report, stream | read | no |
| `GET /api/chains` | read | no |
| `GET /api/proposals` and `GET /api/proposals/{id}` | read | no |
| `POST /api/human/session-handoffs` | human creates | yes |
| `PATCH /api/human/proposals/{id}` | human edits with expected revision | yes |
| `POST /api/human/proposals/{id}/approve` | human applies immediately | yes |
| `POST /api/human/proposals/{id}/reject` | human rejects | yes |
| `PUT /api/human/chains` | direct human chain save with expected digest | yes |
| `GET /api/tools/manifest` | agent read | no |
| `POST /api/tools/call` | agent read/propose dispatch | no |

There is never an agent approval, rejection, execution, pin, unpin, resume, or router-control proxy endpoint.

SSE overview updates include proposal summaries and the current chain digest.

## Agent tools

`airlock-console-tools` is a second stdio MCP server. It stays separate from `airlock-web-tools`, which must work without Console and has different network permissions. The console tools server is a standard-library thin client to `http://127.0.0.1:4783`. It never reads registry files or speaks directly to a router. When Console is absent, it returns: "Airlock Console is not running at 127.0.0.1:4783. Start it with `airlock console`, then try again."

One shared standard-library module owns exact tool definitions, input validation, result projection, and dispatch. Console imports it for the HTTP manifest and call endpoint. The stdio server exposes the same definitions through MCP. The page fetches the manifest and registers the same tools through feature-detected WebMCP; WebMCP handlers call only `/api/tools/call`.

Tools:

- `airlock_list_sessions {}`
- `airlock_get_session {session_id}`
- `airlock_get_session_events {session_id, kind?, model?, since?}`
- `airlock_get_routes {session_id?}`
- `airlock_get_headroom {}`
- `airlock_get_global_failover_chain {}`
- `airlock_list_proposals {session_id?, kind?, status?}`
- `airlock_get_proposal {proposal_id}`
- `airlock_propose_session_handoff {session_id, target_model, reason, allow_metered?}`
- `airlock_propose_restore_root {session_id, reason}`
- `airlock_propose_chain_change {chains, reason}`
- `airlock_list_history {project?, model?, since?, until?, q?, limit?}`
- `airlock_get_history_session {history_id}`
- `airlock_list_subagents {history_id}`
- `airlock_get_subagent_feed {history_id, agent_id}`
- `airlock_get_usage {group?, project?, model?, since?, until?}`
- `airlock_query_history {dimension, project?, model?, provider?, agent_type?, tool?, branch?, entrypoint?, since?, until?, q?, order_by?, descending?, limit?}`

The six history tools are read-only and answer from the session history index, through the same `ConsoleState` methods the HTTP history endpoints use, so both channels agree. Every history result is marked untrusted content: titles, branch names, subagent descriptions, and activity previews were written by models and people. History results never carry a working directory or transcript path; `project` is the directory's last component. When the console runs without a history index the tools answer `history_unavailable`.

Strings, arrays, and maps are bounded, exact schemas reject unknown keys, and results contain only console-public fields. No tool can mutate an existing proposal because a person must review its exact immutable content; the person can edit through the human API before approval.

Console tools are installed automatically for Airlock sessions as a separate MCP server, with `AIRLOCK_CONSOLE_TOOLS=off` as an explicit escape hatch. This may expose unavailable tools when Console is not running, but the error is clear and no background process is started implicitly.

## Phase 2 interface

The page gains two focused surfaces, not a new dashboard.

- A proposal panel attached to the selected session shows the current root or pin, proposed target, route readiness, context fit or uncertainty, metered warning, reason, expiry, revision, and audit trail. Human edits happen inline. One primary button says exactly what it will do, such as "Approve and use Opus" or "Approve and restore Fable". It shows Applying, then the actual result or a safe failure.
- A chain editor lives in the inspector and command palette. It supports ordered routes, direct human save, pending proposal comparison, validation, and the permanent new-sessions-only notice.

The UI never renders a control token, CSRF token, account identifier, raw upstream text, or arbitrary event field.

## Proposal audit

Console-only audit fields: `at`, `kind`, `actor_channel`, `proposal_id`, `proposal_kind`, `session_id`, `revision`, `operation`, `from_model`, `target_model`, `status`, `error_code`.

Kinds: `proposal_created`, `proposal_edited`, `proposal_rejected`, `proposal_expired`, `proposal_apply_started`, `proposal_apply_succeeded`, `proposal_apply_failed`, `proposal_apply_conflicted`, and `chain_directly_applied`.

## Race and failure rules

- Router controls are idempotent.
- A second approval while applying returns conflict; after applied it returns the recorded result.
- If the response is lost after a router applied a pin, retry confirms the same target safely.
- Router disappearance, a changed router instance, a missing schema-v2 control token, a cooling target, or newly excessive context leaves the proposal unapplied with a safe error.
- Router restart gives a new instance ID and token. A proposal never rebinds to it.
- Global chain application revalidates and compare-and-swaps at apply time.
- No pending action survives Console restart.
