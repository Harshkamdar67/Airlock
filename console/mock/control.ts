// The phase 2 human and agent API, mocked with the server's exact shapes and
// error envelopes so the page is developable and testable with no Python.
//
// This mirrors bin/airlock_console.py deliberately, including the parts that
// are easy to get wrong: the error envelope, the CSRF and Origin rules, the
// overloaded 409 conflict, and approve answering 200 with a failed status.

import type { IncomingMessage, ServerResponse } from 'node:http';

type Any = Record<string, any>;

export const MOCK_CSRF_TOKEN = 'dev-csrf-token-not-a-secret';
export const CHAIN_NOTICE =
  'Applies to sessions started after this change. Running sessions keep their frozen chains.';
const EMPTY_DIGEST = 'd598b5c92ec902d244a418ec70cc7262a93731d5553b7163fa3e6e486c51e7c7';

/** The one error envelope the server uses everywhere. */
export function errorBody(type: string, message: string) {
  return { type: 'error', error: { type, message } };
}

function isoNow(offsetSeconds = 0): string {
  return new Date(Date.now() + offsetSeconds * 1000).toISOString().replace(/\.\d+Z$/, 'Z');
}

function digestOf(chains: Any): string {
  // Not the server's canonical sha256, only a stable stand-in of the right
  // shape, so compare-and-swap behaviour is exercised end to end.
  const text = JSON.stringify(chains, Object.keys(chains).sort());
  let hash = 0n;
  for (const char of text) hash = (hash * 131n + BigInt(char.charCodeAt(0))) % (2n ** 64n);
  return hash.toString(16).padStart(16, '0').repeat(4).slice(0, 64);
}

/** All mutable dev state lives here, and resets when the dev server restarts. */
export class MockControlState {
  chains: Any = {
    'claude-fable-5-1[1m]': ['claude-opus-5[1m]', 'gpt-5.6-sol', 'stealth/ox-alpha'],
    'gpt-5.6-sol': ['gpt-5.6-terra', 'claude-opus-5[1m]'],
  };
  proposals: Any[] = [];
  private counter = 0;

  constructor(seed?: Any[]) {
    if (seed) this.proposals = seed.map((item) => ({ ...item }));
  }

  digest(): string {
    return Object.keys(this.chains).length ? digestOf(this.chains) : EMPTY_DIGEST;
  }

  nextId(prefix: string): string {
    this.counter += 1;
    return `${prefix}${String(this.counter).padStart(4, '0')}_devfixture`;
  }

  find(id: string): Any | undefined {
    return this.proposals.find((p) => p.id === id);
  }

  summaries(): Any[] {
    return this.proposals.map((p) => {
      const row: Any = {
        id: p.id,
        kind: p.kind,
        status: p.status,
        created_by: p.created_by,
        created_at: p.created_at,
        expires_at: p.expires_at,
        revision: p.revision,
      };
      if (p.kind === 'session_handoff') {
        row.session_id = p.session_id;
        row.operation = p.operation;
        row.target_model = p.target_model;
      } else {
        row.base_digest = p.base_digest;
      }
      return row;
    });
  }
}

export interface MockRequest {
  method: string;
  path: string;
  headers: IncomingMessage['headers'];
  body: Any | null;
  origin: string | null;
}

export interface MockResponse {
  status: number;
  body: unknown;
}

const HANDOFF_KEYS = new Set([
  'session_id',
  'operation',
  'target_model',
  'reason',
  'allow_metered',
]);
const EDIT_KEYS = new Set([
  'expected_revision',
  'target_model',
  'reason',
  'allow_metered',
  'operation',
  'chains',
  'base_digest',
]);
const CHAIN_KEYS = new Set(['chains', 'expected_digest']);

function unsupported(body: Any, allowed: Set<string>): boolean {
  return Object.keys(body).some((key) => !allowed.has(key));
}

function validReason(value: unknown): boolean {
  return (
    typeof value === 'string' &&
    value.length >= 8 &&
    value.length <= 400 &&
    value.trim().length > 0 &&
    ![...value].some((c) => c.charCodeAt(0) < 32)
  );
}

/**
 * The human channel's preconditions, in the server's order. Returns null when
 * the request may proceed.
 */
export function humanGuard(req: MockRequest): MockResponse | null {
  if (!req.origin) {
    return { status: 403, body: errorBody('forbidden', 'Origin is required') };
  }
  const contentType = String(req.headers['content-type'] ?? '').split(';')[0].trim();
  if (contentType !== 'application/json') {
    return {
      status: 400,
      body: errorBody('invalid_request', 'Content-Type must be application/json'),
    };
  }
  const token = req.headers['x-airlock-csrf'];
  if (typeof token !== 'string' || token !== MOCK_CSRF_TOKEN) {
    return { status: 403, body: errorBody('forbidden', 'CSRF token is invalid') };
  }
  if (req.body === null) {
    return {
      status: 400,
      body: errorBody('invalid_request', 'Request body is not a JSON object'),
    };
  }
  return null;
}

export function handleControl(
  state: MockControlState,
  req: MockRequest,
  detailFor: (sessionId: string) => Any | null,
): MockResponse | null {
  const { method, path } = req;

  if (method === 'GET' && path === '/api/chains') {
    return {
      status: 200,
      body: { chains: state.chains, digest: state.digest(), notice: CHAIN_NOTICE },
    };
  }

  if (method === 'GET' && path === '/api/proposals') {
    return { status: 200, body: { proposals: state.proposals } };
  }

  const one = /^\/api\/proposals\/([^/]+)$/.exec(path);
  if (method === 'GET' && one) {
    const found = state.find(decodeURIComponent(one[1]));
    if (!found) return { status: 404, body: errorBody('not_found', 'Proposal not found') };
    return { status: 200, body: found };
  }

  if (path === '/api/tools/manifest' && method === 'GET') {
    return { status: 200, body: { tools: MOCK_TOOLS } };
  }

  if (path === '/api/tools/call' && method === 'POST') {
    const token = req.headers['x-airlock-csrf'];
    if (typeof token === 'string' && token) {
      return {
        status: 403,
        body: errorBody('forbidden', 'Agent tools cannot carry a CSRF token'),
      };
    }
    const body = req.body ?? {};
    if (unsupported(body, new Set(['name', 'arguments']))) {
      return { status: 400, body: errorBody('invalid_request', 'unsupported fields') };
    }
    const name = body.name;
    if (!MOCK_TOOLS.some((tool) => tool.name === name)) {
      return { status: 404, body: errorBody('unknown_tool', 'unknown tool') };
    }
    if (name === 'airlock_get_global_failover_chain') {
      return { status: 200, body: { chains: state.chains, digest: state.digest() } };
    }
    if (name === 'airlock_list_proposals') {
      return { status: 200, body: { proposals: state.proposals } };
    }
    return { status: 200, body: { ok: true } };
  }

  if (!path.startsWith('/api/human/')) return null;

  const guard = humanGuard(req);
  if (guard) return guard;
  const body = req.body ?? {};

  if (method === 'POST' && path === '/api/human/session-handoffs') {
    if (unsupported(body, HANDOFF_KEYS)) {
      return { status: 400, body: errorBody('invalid_request', 'unsupported fields') };
    }
    if (typeof body.session_id !== 'string' || !/^[A-Za-z0-9._-]{1,64}$/.test(body.session_id)) {
      return { status: 400, body: errorBody('invalid_request', 'session_id is invalid') };
    }
    if (body.operation !== 'pin' && body.operation !== 'restore_root') {
      return { status: 400, body: errorBody('invalid_request', 'operation is invalid') };
    }
    if (!validReason(body.reason)) {
      return { status: 400, body: errorBody('invalid_request', 'reason must be a string') };
    }
    const detail = detailFor(body.session_id);
    if (!detail) return { status: 404, body: errorBody('not_found', 'Session not found') };
    if (detail.controllable !== true) {
      return { status: 403, body: errorBody('forbidden', 'Session is not controllable') };
    }
    if (
      state.proposals.some(
        (p) =>
          p.kind === 'session_handoff' &&
          p.session_id === body.session_id &&
          ['pending', 'applying'].includes(p.status),
      )
    ) {
      return {
        status: 409,
        body: errorBody('active_proposal_exists', 'An active proposal already exists'),
      };
    }
    const route = (detail.routes ?? []).find((r: Any) => r.model === body.target_model);
    if (body.operation === 'pin') {
      if (!route) {
        return {
          status: 400,
          body: errorBody('model_not_enabled', 'Target model is not enabled'),
        };
      }
      if (route.status === 'cooling') {
        return { status: 409, body: errorBody('route_cooling', 'Target route is cooling') };
      }
      if (route.fits_context === false) {
        return {
          status: 409,
          body: errorBody(
            'context_too_large',
            'Conversation does not fit the target window',
          ),
        };
      }
      if (route.metered && body.allow_metered !== true) {
        return {
          status: 400,
          body: errorBody('invalid_request', 'Metered target requires allow_metered'),
        };
      }
    } else if (!detail.pinned_model) {
      return { status: 409, body: errorBody('conflict', 'Session is not pinned') };
    }

    const proposal: Any = {
      id: state.nextId('shp_'),
      kind: 'session_handoff',
      session_id: body.session_id,
      operation: body.operation,
      target_model: body.operation === 'pin' ? body.target_model : null,
      reason: body.reason,
      allow_metered: body.allow_metered === true,
      created_by: 'human',
      created_at: isoNow(),
      expires_at: isoNow(900),
      revision: 1,
      status: 'pending',
      basis: {
        observed_at: isoNow(),
        active_model: detail.active_model ?? null,
        pinned_model: detail.pinned_model ?? null,
        context_input_tokens: detail.context?.input_tokens ?? null,
        route_status: route?.status ?? null,
      },
      application: null,
      last_error: null,
    };
    state.proposals.push(proposal);
    return { status: 200, body: proposal };
  }

  const edit = /^\/api\/human\/proposals\/([^/]+)$/.exec(path);
  if (method === 'PATCH' && edit) {
    const proposal = state.find(decodeURIComponent(edit[1]));
    if (!proposal) return { status: 404, body: errorBody('not_found', 'Proposal not found') };
    if (unsupported(body, EDIT_KEYS)) {
      return { status: 400, body: errorBody('invalid_request', 'unsupported fields') };
    }
    if (typeof body.expected_revision !== 'number' || body.expected_revision < 1) {
      return {
        status: 400,
        body: errorBody('invalid_request', 'expected_revision is invalid'),
      };
    }
    if (!['pending', 'failed', 'conflicted'].includes(proposal.status)) {
      return { status: 409, body: errorBody('conflict', 'Proposal cannot be edited') };
    }
    if (proposal.revision !== body.expected_revision) {
      return {
        status: 409,
        body: errorBody('stale_revision', 'Proposal revision does not match'),
      };
    }
    if (body.reason !== undefined && !validReason(body.reason)) {
      return {
        status: 400,
        body: errorBody('invalid_request', 'reason length is out of bounds'),
      };
    }
    if (proposal.kind === 'session_handoff') {
      if (body.target_model !== undefined) proposal.target_model = body.target_model;
      if (body.allow_metered !== undefined) proposal.allow_metered = body.allow_metered === true;
    } else if (body.chains !== undefined) {
      proposal.chains = body.chains;
    }
    if (body.reason !== undefined) proposal.reason = body.reason;
    proposal.revision += 1;
    proposal.status = 'pending';
    proposal.last_error = null;
    proposal.expires_at = isoNow(900);
    return { status: 200, body: proposal };
  }

  const approve = /^\/api\/human\/proposals\/([^/]+)\/approve$/.exec(path);
  if (method === 'POST' && approve) {
    const proposal = state.find(decodeURIComponent(approve[1]));
    if (!proposal) return { status: 404, body: errorBody('not_found', 'Proposal not found') };
    if (Object.keys(body).length) {
      return {
        status: 400,
        body: errorBody('invalid_request', 'approve accepts no extra fields'),
      };
    }
    if (proposal.status === 'applying') {
      return { status: 409, body: errorBody('application_in_progress', 'Proposal is already applying') };
    }
    if (proposal.status === 'applied') {
      proposal.application = { ...proposal.application, already_applied: true };
      return { status: 200, body: proposal };
    }
    if (!['pending', 'failed', 'conflicted'].includes(proposal.status)) {
      return { status: 409, body: errorBody('route_state_changed', 'Proposal cannot be applied') };
    }

    // A fixture may ask for a specific outcome so every rendered state is
    // reachable in the browser without a real router.
    const forced = proposal.__force as string | undefined;
    const application: Any = {
      attempts: (proposal.application?.attempts ?? 0) + 1,
      started_at: isoNow(),
      finished_at: isoNow(),
      router_instance_id: proposal.kind === 'chain_change' ? null : 'r-devmock',
      previous_pinned_model: null,
      pinned_model: proposal.kind === 'chain_change' ? null : proposal.target_model,
      changed: true,
      already_applied: false,
    };
    if (forced === 'conflicted' || forced === 'failed') {
      proposal.status = forced;
      proposal.application = { ...application, changed: false };
      proposal.last_error = {
        code: forced === 'conflicted' ? 'route_cooling' : 'failed',
        message:
          forced === 'conflicted'
            ? 'Target route is cooling'
            : 'Proposal could not be applied',
      };
      return { status: 200, body: proposal };
    }

    proposal.status = 'applied';
    proposal.application = application;
    proposal.last_error = null;
    if (proposal.kind === 'chain_change') state.chains = proposal.chains;
    return { status: 200, body: proposal };
  }

  const reject = /^\/api\/human\/proposals\/([^/]+)\/reject$/.exec(path);
  if (method === 'POST' && reject) {
    const proposal = state.find(decodeURIComponent(reject[1]));
    if (!proposal) return { status: 404, body: errorBody('not_found', 'Proposal not found') };
    if (Object.keys(body).length) {
      return {
        status: 400,
        body: errorBody('invalid_request', 'reject accepts no extra fields'),
      };
    }
    if (proposal.status !== 'pending') {
      return { status: 409, body: errorBody('conflict', 'Proposal cannot be rejected') };
    }
    proposal.status = 'rejected';
    proposal.last_error = null;
    return { status: 200, body: proposal };
  }

  if (method === 'PUT' && path === '/api/human/chains') {
    if (unsupported(body, CHAIN_KEYS)) {
      return { status: 400, body: errorBody('invalid_request', 'unsupported fields') };
    }
    if (typeof body.expected_digest !== 'string' || !/^[0-9a-f]{64}$/.test(body.expected_digest)) {
      return {
        status: 400,
        body: errorBody('invalid_request', 'expected digest is invalid'),
      };
    }
    if (body.expected_digest !== state.digest()) {
      // The real server returns no chains here, so the page must re-read.
      return {
        status: 409,
        body: {
          ...errorBody('chain_conflict', 'failover chain changed during this operation'),
          current: {
            chains: state.chains,
            digest: state.digest(),
            notice: CHAIN_NOTICE,
          },
        },
      };
    }
    const next = body.chains ?? {};
    const changed = JSON.stringify(next) !== JSON.stringify(state.chains);
    state.chains = next;
    return {
      status: 200,
      body: {
        chains: state.chains,
        digest: state.digest(),
        notice: CHAIN_NOTICE,
        changed,
      },
    };
  }

  return { status: 404, body: errorBody('not_found', 'Route not found') };
}

/** The same names the console tools helper exposes. No approve or apply. */
export const MOCK_TOOLS = [
  'airlock_list_sessions',
  'airlock_get_session',
  'airlock_get_session_events',
  'airlock_get_routes',
  'airlock_get_headroom',
  'airlock_get_global_failover_chain',
  'airlock_list_proposals',
  'airlock_get_proposal',
  'airlock_propose_session_handoff',
  'airlock_propose_restore_root',
  'airlock_propose_chain_change',
  'airlock_list_history',
  'airlock_get_history_session',
  'airlock_list_subagents',
  'airlock_get_subagent_feed',
  'airlock_get_usage',
  'airlock_query_history',
].map((name) => ({
  name,
  description: `Airlock console tool ${name}.`,
  inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  annotations: { readOnlyHint: !name.startsWith('airlock_propose') },
}));

export function readRequestBody(req: IncomingMessage): Promise<Any | null> {
  return new Promise((resolvePromise) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => {
      const text = Buffer.concat(chunks).toString('utf8');
      if (!text) return resolvePromise({});
      try {
        const parsed = JSON.parse(text);
        resolvePromise(parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null);
      } catch {
        resolvePromise(null);
      }
    });
    req.on('error', () => resolvePromise(null));
  });
}

export function sendJson(res: ServerResponse, status: number, body: unknown): void {
  const text = JSON.stringify(body);
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.end(text);
}
