// The phase 2 API client: header discipline, the CSRF rule, and the failure
// shapes the page has to distinguish. These run without a browser by driving
// the module's own fetch.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  approveProposal,
  callTool,
  createHandoffProposal,
  editProposal,
  getChains,
  getProposals,
  rejectProposal,
  saveChains,
} from '../src/lib/control';
import { csrfToken, humanApiAvailable, mutationHeaders } from '../src/lib/csrf';

interface Call {
  url: string;
  init: RequestInit;
}

const calls: Call[] = [];
let nextResponse: { status: number; body: unknown } = { status: 200, body: {} };

function metaWith(token: string | null) {
  return {
    querySelector: (selector: string) =>
      selector === 'meta[name="airlock-csrf"]' && token !== undefined
        ? { getAttribute: () => (token === null ? null : token) }
        : null,
  } as unknown as Document;
}

beforeEach(() => {
  calls.length = 0;
  nextResponse = { status: 200, body: {} };
  (globalThis as any).document = metaWith('token-abc');
  (globalThis as any).fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
    return {
      ok: nextResponse.status >= 200 && nextResponse.status < 300,
      status: nextResponse.status,
      json: async () => nextResponse.body,
    } as Response;
  });
});

afterEach(() => {
  delete (globalThis as any).document;
  delete (globalThis as any).fetch;
});

const lastHeaders = () => (calls.at(-1)!.init.headers ?? {}) as Record<string, string>;

describe('the CSRF token', () => {
  it('is read from the injected meta and nowhere else', () => {
    expect(csrfToken()).toBe('token-abc');
    expect(humanApiAvailable()).toBe(true);
    expect(mutationHeaders()).toEqual({
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'X-Airlock-CSRF': 'token-abc',
    });
  });

  it('is absent when the page was not served by a console', () => {
    (globalThis as any).document = metaWith(null);
    expect(csrfToken()).toBeNull();
    expect(humanApiAvailable()).toBe(false);
    expect(mutationHeaders()).toBeNull();
  });

  it('fails closed rather than sending a mutation that will be refused', async () => {
    (globalThis as any).document = metaWith(null);
    await expect(approveProposal('shp_1')).rejects.toMatchObject({ code: 'forbidden' });
    expect(calls).toHaveLength(0);
  });
});

describe('request shapes', () => {
  it('sends the token in the header, never in the URL or the body', async () => {
    await approveProposal('shp_1');
    const call = calls.at(-1)!;
    expect(call.url).toBe('/api/human/proposals/shp_1/approve');
    expect(call.url).not.toContain('token-abc');
    expect(String(call.init.body)).not.toContain('token-abc');
    expect(lastHeaders()['X-Airlock-CSRF']).toBe('token-abc');
    expect(call.init.method).toBe('POST');
    expect(call.init.credentials).toBe('omit');
  });

  it('never sets Origin by hand, so the browser sends the real one', async () => {
    await approveProposal('shp_1');
    expect(Object.keys(lastHeaders())).not.toContain('Origin');
    expect(Object.keys(lastHeaders())).not.toContain('origin');
  });

  it('uses a same-origin relative path for every mutation', async () => {
    await rejectProposal('shp_1');
    await saveChains({}, 'a'.repeat(64));
    await editProposal('shp_1', { expected_revision: 2, reason: 'Because it fits.' });
    for (const call of calls) expect(call.url.startsWith('/api/')).toBe(true);
  });

  it('omits the target and the metered flag on a restore_root proposal', async () => {
    await createHandoffProposal({
      session_id: 'r-8f2c1a',
      operation: 'restore_root',
      reason: 'Return the session to its root model.',
    });
    expect(JSON.parse(String(calls.at(-1)!.init.body))).toEqual({
      session_id: 'r-8f2c1a',
      operation: 'restore_root',
      reason: 'Return the session to its root model.',
    });
  });

  it('sends exactly the keys the server allows for a pin', async () => {
    await createHandoffProposal({
      session_id: 'r-8f2c1a',
      operation: 'pin',
      target_model: 'stealth/ox-alpha',
      allow_metered: true,
      reason: 'Ox-alpha is ready and fits.',
    });
    expect(Object.keys(JSON.parse(String(calls.at(-1)!.init.body))).sort()).toEqual([
      'allow_metered',
      'operation',
      'reason',
      'session_id',
      'target_model',
    ]);
  });

  it('always carries expected_revision on an edit, and never operation', async () => {
    await editProposal('shp_1', {
      expected_revision: 3,
      target_model: 'gpt-5.6-sol',
      reason: 'Sol has room now.',
      allow_metered: false,
    });
    const body = JSON.parse(String(calls.at(-1)!.init.body));
    expect(body.expected_revision).toBe(3);
    expect(body).not.toHaveProperty('operation');
  });

  it('names the chain digest expected_digest on save', async () => {
    await saveChains({ 'gpt-5.6-sol': ['gpt-5.6-terra'] }, 'b'.repeat(64));
    expect(JSON.parse(String(calls.at(-1)!.init.body))).toEqual({
      chains: { 'gpt-5.6-sol': ['gpt-5.6-terra'] },
      expected_digest: 'b'.repeat(64),
    });
  });

  it('sends no CSRF header on the agent tool channel', async () => {
    await callTool('airlock_list_sessions', {});
    expect(Object.keys(lastHeaders())).not.toContain('X-Airlock-CSRF');
    expect(JSON.parse(String(calls.at(-1)!.init.body))).toEqual({
      name: 'airlock_list_sessions',
      arguments: {},
    });
  });
});

describe('reads', () => {
  it('unwraps the proposals envelope', async () => {
    nextResponse = { status: 200, body: { proposals: [{ id: 'shp_1' }] } };
    expect(await getProposals()).toEqual([{ id: 'shp_1' }]);
  });

  it('tolerates a server that sends no proposals key', async () => {
    nextResponse = { status: 200, body: {} };
    expect(await getProposals()).toEqual([]);
  });

  it('reads chains with their digest and notice', async () => {
    nextResponse = {
      status: 200,
      body: { chains: {}, digest: 'c'.repeat(64), notice: 'Applies to sessions...' },
    };
    expect((await getChains()).digest).toBe('c'.repeat(64));
  });
});

describe('failures', () => {
  it('surfaces the server error envelope, not the raw status', async () => {
    nextResponse = {
      status: 403,
      body: { type: 'error', error: { type: 'forbidden', message: 'CSRF token is invalid' } },
    };
    await expect(approveProposal('shp_1')).rejects.toMatchObject({
      status: 403,
      code: 'forbidden',
      message: 'CSRF token is invalid',
    });
  });

  it('recognises stale_revision by exact error type', async () => {
    nextResponse = {
      status: 409,
      body: {
        type: 'error',
        error: { type: 'stale_revision', message: 'Proposal revision does not match' },
      },
    };
    const error = await editProposal('shp_1', { expected_revision: 1 }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe('stale_revision');
    expect(error.isStaleRevision).toBe(true);
    expect(error.isChainConflict).toBe(false);
  });

  it('keeps the old stale-revision message match only as a compatibility fallback', async () => {
    nextResponse = {
      status: 409,
      body: {
        type: 'error',
        error: { type: 'conflict', message: 'Proposal revision does not match' },
      },
    };
    const error = await editProposal('shp_1', { expected_revision: 1 }).catch((e) => e);
    expect(error.isStaleRevision).toBe(true);
  });

  it('recognises chain_conflict by exact type and carries the current snapshot', async () => {
    const current = {
      chains: { 'gpt-5.6-sol': ['gpt-5.6-terra'] },
      digest: 'e'.repeat(64),
      notice: 'Applies to sessions started after this change.',
    };
    nextResponse = {
      status: 409,
      body: {
        type: 'error',
        error: {
          type: 'chain_conflict',
          message: 'failover chain changed during this operation',
        },
        current,
      },
    };
    const error = await saveChains({}, 'd'.repeat(64)).catch((e) => e);
    expect(error.code).toBe('chain_conflict');
    expect(error.isChainConflict).toBe(true);
    expect(error.isStaleRevision).toBe(false);
    expect(error.current).toEqual(current);
  });

  it('keeps the old chain-conflict message match as a fallback, without a snapshot', async () => {
    nextResponse = {
      status: 409,
      body: {
        type: 'error',
        error: { type: 'conflict', message: 'failover chain changed during this operation' },
      },
    };
    const error = await saveChains({}, 'd'.repeat(64)).catch((e) => e);
    expect(error.isChainConflict).toBe(true);
    expect(error.current).toBeNull();
  });

  it('names application_in_progress and route_state_changed exactly', () => {
    const applying = new ApiError(
      409,
      'application_in_progress',
      'Proposal is already applying',
    );
    const changed = new ApiError(
      409,
      'route_state_changed',
      'Proposal cannot be applied',
    );
    expect(applying.isApplicationInProgress).toBe(true);
    expect(applying.isRouteStateChanged).toBe(false);
    expect(changed.isRouteStateChanged).toBe(true);
    expect(changed.isApplicationInProgress).toBe(false);
  });

  it('recognises the lowercase active-proposal error the server emits', async () => {
    nextResponse = {
      status: 409,
      body: {
        type: 'error',
        error: {
          type: 'active_proposal_exists',
          message: 'An active proposal already exists',
        },
      },
    };
    const error = await createHandoffProposal({
      session_id: 'r-1',
      operation: 'restore_root',
      reason: 'Return to the root.',
    }).catch((e) => e);
    expect(error.isActiveProposal).toBe(true);
  });

  it('still recognises the old uppercase error code as a client fallback', async () => {
    nextResponse = {
      status: 409,
      body: {
        type: 'error',
        error: {
          type: 'ACTIVE_PROPOSAL_EXISTS',
          message: 'An active proposal already exists',
        },
      },
    };
    const error = await createHandoffProposal({
      session_id: 'r-1',
      operation: 'restore_root',
      reason: 'Return to the root.',
    }).catch((e) => e);
    expect(error.isActiveProposal).toBe(true);
  });

  it('does not treat a non-JSON error body as content to show', async () => {
    (globalThis as any).fetch = vi.fn(async () => ({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error('not json');
      },
    }));
    await expect(approveProposal('shp_1')).rejects.toMatchObject({
      status: 500,
      code: 'unknown',
    });
  });

  it('reports an approve that answered 200 with a failed status as data, not an error', async () => {
    nextResponse = {
      status: 200,
      body: {
        id: 'shp_1',
        status: 'conflicted',
        last_error: { code: 'route_cooling', message: 'Target route is cooling' },
      },
    };
    const result = (await approveProposal('shp_1')) as any;
    expect(result.status).toBe('conflicted');
    expect(result.last_error.code).toBe('route_cooling');
  });
});
