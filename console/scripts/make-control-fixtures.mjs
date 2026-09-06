// Phase 2 fixtures: one scenario per rendered proposal state, plus a chain
// proposal to compare against. The mock seeds its state from these.
import { writeFileSync, readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const F = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'fixtures');
const write = (name, data) => {
  writeFileSync(resolve(F, name), `${JSON.stringify(data, null, 2)}\n`);
  console.log('wrote', name);
};

const AT = '2026-09-05T09:00:00Z';
const EXPIRES = '2026-09-05T09:15:00Z';

const handoff = (o) => ({
  id: o.id,
  kind: 'session_handoff',
  session_id: o.session_id ?? 'r-8f2c1a',
  operation: o.operation ?? 'pin',
  target_model: o.operation === 'restore_root' ? null : (o.target_model ?? 'stealth/ox-alpha'),
  reason: o.reason,
  allow_metered: o.allow_metered ?? false,
  created_by: o.created_by ?? 'agent',
  created_at: o.created_at ?? AT,
  expires_at: o.expires_at ?? EXPIRES,
  revision: o.revision ?? 1,
  status: o.status ?? 'pending',
  basis: {
    observed_at: AT,
    active_model: o.active_model ?? 'claude-fable-5-1[1m]',
    pinned_model: o.pinned_model ?? null,
    context_input_tokens: o.context ?? 612340,
    route_status: o.route_status ?? 'ready',
  },
  application: o.application ?? null,
  last_error: o.last_error ?? null,
  ...(o.force ? { __force: o.force } : {}),
});

const application = (o = {}) => ({
  attempts: o.attempts ?? 1,
  started_at: o.started_at ?? '2026-09-05T09:01:00Z',
  finished_at: o.finished_at ?? '2026-09-05T09:01:02Z',
  router_instance_id: 'r-8f2c1a',
  previous_pinned_model: o.previous ?? null,
  pinned_model: o.pinned ?? 'stealth/ox-alpha',
  changed: o.changed ?? true,
  already_applied: o.already_applied ?? false,
});

// 1. A pending agent proposal on the blocked session, the headline case.
//    Ox-alpha is metered, and the server refuses to create a metered target
//    without allow_metered, so the agent must have accepted the extra usage.
write('proposals-proposal.json', [
  handoff({
    id: 'shp_pending_devfixture',
    allow_metered: true,
    reason:
      'Fable and its whole chain are cooling. Ox-alpha is ready and fits this 612k conversation.',
  }),
]);

// 2. Applied, so the audit trail and the recorded result render.
write('proposals-applied.json', [
  handoff({
    id: 'shp_applied_devfixture',
    status: 'applied',
    allow_metered: true,
    reason: 'Ox-alpha was ready and large enough, so the session moved there.',
    application: application(),
  }),
]);

// 3. Conflicted: approve happened, the router refused, and a safe error stands.
write('proposals-conflict.json', [
  handoff({
    id: 'shp_conflicted_devfixture',
    status: 'conflicted',
    revision: 2,
    reason: 'Move the session to Sol now that the Anthropic plan is cooling.',
    target_model: 'gpt-5.6-sol',
    route_status: 'ready',
    application: application({ changed: false, pinned: null }),
    last_error: {
      code: 'context_too_large',
      message: 'Conversation does not fit the target window',
    },
  }),
]);

// 4. Every remaining status at once, so no state is unreachable in a browser.
write('proposals-states.json', [
  handoff({
    id: 'shp_pending_devfixture',
    allow_metered: true,
    reason: 'A pending proposal awaiting a decision.',
  }),
  handoff({
    id: 'shp_applying_devfixture',
    status: 'applying',
    allow_metered: true,
    reason: 'An approval that is being applied to the router right now.',
    created_at: '2026-09-05T08:59:00Z',
  }),
  handoff({
    id: 'shp_failed_devfixture',
    status: 'failed',
    allow_metered: true,
    revision: 3,
    reason: 'The router could not be reached when this was approved.',
    created_at: '2026-09-05T08:58:00Z',
    last_error: { code: 'not_found', message: 'Router is not reachable' },
  }),
  handoff({
    id: 'shp_rejected_devfixture',
    status: 'rejected',
    allow_metered: true,
    reason: 'A proposal a person looked at and turned down.',
    created_at: '2026-09-05T08:57:00Z',
  }),
  handoff({
    id: 'shp_expired_devfixture',
    status: 'expired',
    allow_metered: true,
    reason: 'A proposal nobody decided on within fifteen minutes.',
    created_at: '2026-09-05T08:40:00Z',
    expires_at: '2026-09-05T08:55:00Z',
    last_error: { code: 'expired', message: 'Proposal expired before approval' },
  }),
  handoff({
    id: 'shp_applied2_devfixture',
    status: 'applied',
    allow_metered: true,
    reason: 'An applied proposal, kept visible for an hour.',
    created_at: '2026-09-05T08:56:00Z',
    application: application({ already_applied: true }),
  }),
]);

// 5. A pending global chain change, for the editor's comparison view.
write('proposals-chains.json', [
  {
    id: 'ccp_pending_devfixture',
    kind: 'chain_change',
    chains: {
      'claude-fable-5-1[1m]': ['claude-opus-5[1m]', 'grok-4.6', 'stealth/ox-alpha'],
      'gpt-5.6-sol': ['gpt-5.6-terra'],
    },
    base_digest: null,
    reason: 'Prefer Grok before a metered OpenRouter route when Anthropic is cooling.',
    created_by: 'agent',
    created_at: AT,
    expires_at: EXPIRES,
    revision: 1,
    status: 'pending',
    application: null,
    last_error: null,
  },
]);

const BASE_CHAINS = {
  'claude-fable-5-1[1m]': ['claude-opus-5[1m]', 'gpt-5.6-sol', 'stealth/ox-alpha'],
  'gpt-5.6-sol': ['gpt-5.6-terra', 'claude-opus-5[1m]'],
};
write('chains-chains.json', BASE_CHAINS);
write('chains-proposal.json', BASE_CHAINS);

// A pinned session, so `Restore session root` is reachable.
const blocked = JSON.parse(readFileSync(resolve(F, 'overview.json'), 'utf8'));
const pinnedSession = {
  ...blocked.sessions[0],
  active_model: 'stealth/ox-alpha',
};
write('overview-pinned.json', {
  ...blocked,
  sessions: [pinnedSession, ...blocked.sessions.slice(1)],
});

const detail = JSON.parse(readFileSync(resolve(F, 'session-r-8f2c1a.json'), 'utf8'));
write('session-pinned-r-8f2c1a.json', {
  ...detail,
  active_model: 'stealth/ox-alpha',
  pinned_model: 'stealth/ox-alpha',
  controllable: true,
});

// A session that cannot be controlled at all: a v1 registry row.
write('overview-uncontrollable.json', {
  ...blocked,
  sessions: [blocked.sessions[0]],
});
write('session-uncontrollable-r-8f2c1a.json', {
  ...detail,
  controllable: false,
});
write('proposals-uncontrollable.json', [
  handoff({
    id: 'shp_locked_devfixture',
    allow_metered: true,
    reason: 'An agent proposed this before the session lost its control channel.',
  }),
]);

// A router installed before this release: no workdir, and no route metadata.
const bare = {
  ...blocked.sessions[0],
  id: 'r-e37907abc',
  workdir: '',
  project: '',
  context: { input_tokens: null, window: null },
};
write('overview-bare.json', {
  ...blocked,
  sessions: [bare],
  routes: blocked.routes.map((r) => ({
    ...r,
    category: 'unknown',
    context_window: null,
  })),
  attention: [
    {
      kind: 'session_blocked',
      session_id: 'r-e37907abc',
      since: '2026-09-05T08:58:40Z',
      summary: 'Every route in the chain is cooling',
    },
  ],
});
write('session-bare-r-e37907abc.json', {
  ...detail,
  id: 'r-e37907abc',
  workdir: '',
  project: '',
  started_at: '',
  context: { input_tokens: null, window: null },
  routes: detail.routes.map((r) => ({
    ...r,
    category: 'unknown',
    context_window: null,
    fits_context: null,
  })),
  controllable: true,
});

// A metered target that somehow arrives without consent. The server refuses to
// create this, so it exists only to prove the page's safety net.
write('proposals-metered.json', [
  handoff({
    id: 'shp_metered_devfixture',
    allow_metered: false,
    reason: 'Ox-alpha is the only ready route large enough for this conversation.',
  }),
]);
