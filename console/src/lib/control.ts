// The phase 2 API client: reads, and the four human mutations.
//
// Every mutation is an exact same-origin fetch, so the browser sends the
// console's own Origin. The CSRF token comes from the injected meta and is
// placed in the X-Airlock-CSRF header only. It never reaches a URL, a log, a
// report, a state export, or a tool call.
//
// /api/tools/call is the inverse: the server rejects a request that carries a
// CSRF header at all, so the agent path deliberately sends none.

import { withFixture } from './api';
import { mutationHeaders } from './csrf';
import type {
  ApiErrorBody,
  ChainSnapshot,
  Proposal,
  ProposalOperation,
  SessionHandoffProposal,
  ToolDefinition,
} from '../types';

/** A safe, typed failure. `code` is the server's error.type literal. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly current: ChainSnapshot | null;

  constructor(
    status: number,
    code: string,
    message: string,
    current: ChainSnapshot | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.current = current;
  }

  /**
   * The server overloads 409 conflict across five causes and distinguishes
   * them only by message, so these read the message deliberately.
   */
  get isStaleRevision(): boolean {
    return (
      this.code === 'stale_revision' ||
      (this.code === 'conflict' && /revision does not match/i.test(this.message))
    );
  }

  get isChainConflict(): boolean {
    return (
      this.code === 'chain_conflict' ||
      (this.code === 'conflict' && /failover chain changed/i.test(this.message))
    );
  }

  get isApplicationInProgress(): boolean {
    return this.code === 'application_in_progress';
  }

  get isRouteStateChanged(): boolean {
    return this.code === 'route_state_changed';
  }

  get isActiveProposal(): boolean {
    return this.code === 'active_proposal_exists' || this.code === 'ACTIVE_PROPOSAL_EXISTS';
  }
}

export const MISSING_CSRF = new ApiError(
  0,
  'forbidden',
  'This page was not served by an Airlock console, so it cannot make changes.',
);

async function readError(res: Response): Promise<ApiError> {
  let code = 'unknown';
  let message = `The console answered ${res.status}.`;
  let current: ChainSnapshot | null = null;
  try {
    const body = (await res.json()) as ApiErrorBody;
    if (body?.error?.type) code = body.error.type;
    if (body?.error?.message) message = body.error.message;
    if (body?.current?.chains && typeof body.current.digest === 'string') {
      current = body.current;
    }
  } catch {
    // A non-JSON body is not shown; the status already says enough.
  }
  return new ApiError(res.status, code, message, current);
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(withFixture(path), {
    headers: { Accept: 'application/json' },
    credentials: 'omit',
    signal,
  });
  if (!res.ok) throw await readError(res);
  return (await res.json()) as T;
}

/**
 * One place builds every human mutation, so the header set cannot drift.
 * `Origin` is not set by hand: the browser sends the page's real origin, which
 * is what the server compares against.
 */
async function humanMutation<T>(
  method: 'POST' | 'PATCH' | 'PUT',
  path: string,
  body: unknown,
): Promise<T> {
  const headers = mutationHeaders();
  if (!headers) throw MISSING_CSRF;
  const res = await fetch(withFixture(path), {
    method,
    headers,
    credentials: 'omit',
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) throw await readError(res);
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export function getChains(signal?: AbortSignal): Promise<ChainSnapshot> {
  return getJson<ChainSnapshot>('/api/chains', signal);
}

/**
 * The server ignores query parameters here, so filtering is done on the page
 * rather than pretending the endpoint supports it.
 */
export async function getProposals(signal?: AbortSignal): Promise<Proposal[]> {
  const body = await getJson<{ proposals?: Proposal[] }>('/api/proposals', signal);
  return body.proposals ?? [];
}

export function getProposal(id: string, signal?: AbortSignal): Promise<Proposal> {
  return getJson<Proposal>(`/api/proposals/${encodeURIComponent(id)}`, signal);
}

export function getToolManifest(signal?: AbortSignal): Promise<ToolDefinition[]> {
  return getJson<{ tools?: ToolDefinition[] }>('/api/tools/manifest', signal).then(
    (body) => body.tools ?? [],
  );
}

// ---------------------------------------------------------------------------
// Human mutations
// ---------------------------------------------------------------------------

export interface CreateHandoffInput {
  session_id: string;
  operation: ProposalOperation;
  reason: string;
  target_model?: string | null;
  allow_metered?: boolean;
}

/**
 * The server rejects unknown keys, and rejects a target on restore_root, so
 * the body is assembled explicitly rather than spread from a form object.
 */
export function createHandoffProposal(
  input: CreateHandoffInput,
): Promise<SessionHandoffProposal> {
  const body: Record<string, unknown> = {
    session_id: input.session_id,
    operation: input.operation,
    reason: input.reason,
  };
  if (input.operation === 'pin') {
    body.target_model = input.target_model ?? null;
    body.allow_metered = input.allow_metered === true;
  }
  return humanMutation<SessionHandoffProposal>(
    'POST',
    '/api/human/session-handoffs',
    body,
  );
}

export interface EditProposalInput {
  expected_revision: number;
  target_model?: string | null;
  reason?: string;
  allow_metered?: boolean;
  chains?: Record<string, string[]>;
  base_digest?: string | null;
}

/**
 * `operation` is deliberately never sent: the server does not re-validate it
 * on edit, so changing it through this path is unsafe. Change the operation by
 * rejecting and creating a new proposal.
 */
export function editProposal(id: string, input: EditProposalInput): Promise<Proposal> {
  const body: Record<string, unknown> = { expected_revision: input.expected_revision };
  if (input.target_model !== undefined) body.target_model = input.target_model;
  if (input.reason !== undefined) body.reason = input.reason;
  if (input.allow_metered !== undefined) body.allow_metered = input.allow_metered;
  if (input.chains !== undefined) body.chains = input.chains;
  if (input.base_digest !== undefined) body.base_digest = input.base_digest;
  return humanMutation<Proposal>(
    'PATCH',
    `/api/human/proposals/${encodeURIComponent(id)}`,
    body,
  );
}

/**
 * Approve answers 200 even when the apply failed: the returned proposal
 * carries status applied, conflicted, or failed, plus a safe last_error.
 * Callers must read the status, not just the HTTP result.
 */
export function approveProposal(id: string): Promise<Proposal> {
  return humanMutation<Proposal>(
    'POST',
    `/api/human/proposals/${encodeURIComponent(id)}/approve`,
    {},
  );
}

export function rejectProposal(id: string): Promise<Proposal> {
  return humanMutation<Proposal>(
    'POST',
    `/api/human/proposals/${encodeURIComponent(id)}/reject`,
    {},
  );
}

/**
 * Direct human chain save. The 409 conflict body carries no chains, so the
 * caller must re-read GET /api/chains before offering to save again.
 */
export function saveChains(
  chains: Record<string, string[]>,
  expectedDigest: string,
): Promise<ChainSnapshot> {
  return humanMutation<ChainSnapshot>('PUT', '/api/human/chains', {
    chains,
    expected_digest: expectedDigest,
  });
}

// ---------------------------------------------------------------------------
// Agent tool dispatch
// ---------------------------------------------------------------------------

/**
 * The only endpoint a WebMCP handler may call. No CSRF header: the server
 * answers 403 when the agent channel carries one.
 */
export async function callTool(
  name: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  const res = await fetch(withFixture('/api/tools/call'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    credentials: 'omit',
    body: JSON.stringify({ name, arguments: args ?? {} }),
  });
  if (!res.ok) throw await readError(res);
  return await res.json();
}
