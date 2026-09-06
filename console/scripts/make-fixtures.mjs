// Writes the extra fixtures. The two hand written ones, overview.json and
// session-r-8f2c1a.json, are never touched: they are the contract the server
// worker is building against.
import { writeFileSync, readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const F = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'fixtures');
const base = JSON.parse(readFileSync(resolve(F, 'overview.json'), 'utf8'));
const write = (name, data) => {
  writeFileSync(resolve(F, name), `${JSON.stringify(data, null, 2)}\n`);
  console.log('wrote', name);
};

const AT = '2026-09-05T09:02:12Z';
const route = (o) => ({
  model: o.model,
  short_name: o.short,
  provider: o.provider,
  category: o.category ?? 'included',
  metered: o.metered ?? false,
  context_window: o.window ?? null,
  effort_ceiling: o.effort ?? 'high',
  status: o.status ?? 'ready',
  cooldown_remaining_seconds: o.cooldown ?? null,
  sessions_using: o.using ?? [],
});

const READY_ROUTES = [
  route({ model: 'claude-fable-5-1[1m]', short: 'fable', provider: 'anthropic', category: 'metered', metered: true, window: 1000000, effort: 'max' }),
  route({ model: 'claude-sonnet-5[1m]', short: 'sonnet', provider: 'anthropic', window: 1000000, effort: 'max' }),
  route({ model: 'gpt-5.6-sol', short: 'sol', provider: 'openai', window: 400000, effort: 'xhigh' }),
  route({ model: 'gpt-5.6-luna', short: 'luna', provider: 'openai', window: 400000 }),
  route({ model: 'grok-4.6', short: 'grok', provider: 'grok', window: 2000000 }),
];

const HEADROOM_KNOWN = base.headroom;
const HEADROOM_CLEAR = [
  { provider: 'anthropic', window: '5h', used_percent: 12, resets_at: '2026-09-05T11:00:00Z', source: 'status_line' },
  { provider: 'openai', window: '5h', used_percent: 38, resets_at: '2026-09-05T12:15:00Z', source: 'status_line' },
  { provider: 'grok', window: '5h', used_percent: null, resets_at: null, source: 'unknown' },
  { provider: 'openrouter', window: 'credits', used_percent: null, resets_at: null, source: 'unknown' },
];
const HEADROOM_UNKNOWN = ['anthropic', 'openai', 'grok', 'openrouter'].map((provider) => ({
  provider,
  window: provider === 'openrouter' ? 'credits' : '5h',
  used_percent: null,
  resets_at: null,
  source: 'unknown',
}));

const session = (o) => ({
  id: o.id,
  state: o.state,
  blocked_reason: o.blocked_reason ?? null,
  profile: o.profile ?? 'hybrid-anthropic-root',
  root_model: o.model,
  root_provider: o.provider,
  active_model: o.model,
  workdir: o.workdir,
  project: o.workdir.split(/[\\/]/).filter(Boolean).pop(),
  started_at: o.started ?? '2026-09-05T07:00:00Z',
  last_activity_at: o.last ?? '2026-09-05T09:01:00Z',
  context: o.context === null ? null : { input_tokens: o.tokens ?? 120000, window: o.window ?? 1000000 },
  workers: o.workers ?? [],
  recent_handoffs: o.handoffs ?? 0,
  url: o.url ?? 'http://127.0.0.1:40000',
});

// 1. Empty machine. No sessions, so no routes either; headroom is still a slot.
write('overview-empty.json', {
  generated_at: AT,
  console_version: '0.1.0',
  sessions: [],
  routes: [],
  headroom: HEADROOM_UNKNOWN,
  attention: [],
});

// 2. One session, running, nothing wrong.
write('overview-one.json', {
  generated_at: AT,
  console_version: '0.1.0',
  sessions: [
    session({
      id: 'r-2b77e0',
      state: 'running',
      model: 'claude-sonnet-5[1m]',
      provider: 'anthropic',
      workdir: 'C:\\Users\\Harsh kamdar\\Desktop\\Opensource\\airlock-relay',
      started: '2026-09-05T08:12:50Z',
      last: '2026-09-05T09:02:05Z',
      tokens: 88210,
      workers: [{ model: 'gpt-5.6-luna', requests: 14 }],
      url: 'http://127.0.0.1:41880',
    }),
  ],
  routes: READY_ROUTES.map((r) =>
    r.model === 'claude-sonnet-5[1m]' ? { ...r, sessions_using: ['r-2b77e0'] } : r,
  ),
  headroom: HEADROOM_CLEAR,
  attention: [],
});

// 3. Twelve sessions across every state, to check the rail's density and its
//    keyboard walk across group boundaries.
const PROJECTS = [
  ['claudex', 'blocked', 'rate_limit'],
  ['airlock-relay', 'blocked', 'context_overflow'],
  ['agentquant', 'running', null],
  ['ornith-serve', 'running', null],
  ['pipeline-tools', 'running', null],
  ['dotfiles', 'idle', null],
  ['notes', 'idle', null],
  ['scratch', 'idle', null],
  ['website', 'idle', null],
  ['fixtures-lab', 'idle', null],
  ['old-experiment', 'ended', null],
  ['spike-router', 'ended', null],
];
const MODELS = [
  ['claude-fable-5-1[1m]', 'anthropic', 1000000],
  ['gpt-5.6-sol', 'openai', 400000],
  ['claude-sonnet-5[1m]', 'anthropic', 1000000],
  ['grok-4.6', 'grok', 2000000],
];
write('overview-twelve.json', {
  generated_at: AT,
  console_version: '0.1.0',
  sessions: PROJECTS.map(([project, state, reason], i) => {
    const [model, provider, window] = MODELS[i % MODELS.length];
    return session({
      id: `r-${(0x100000 + i * 7919).toString(16)}`,
      state,
      blocked_reason: reason,
      model,
      provider,
      window,
      tokens: Math.round(window * (0.08 + (i % 9) * 0.1)),
      workdir: `D:\\work\\${project}`,
      started: new Date(Date.parse(AT) - (i + 1) * 40 * 60000).toISOString().replace(/\.\d+Z$/, 'Z'),
      last: new Date(Date.parse(AT) - (i + 1) * 137 * 1000).toISOString().replace(/\.\d+Z$/, 'Z'),
      workers: i % 3 === 0 ? [{ model: 'gpt-5.6-luna', requests: 3 + i }] : [],
      handoffs: i % 4,
    });
  }),
  routes: base.routes,
  headroom: HEADROOM_KNOWN,
  attention: [
    {
      kind: 'session_blocked',
      session_id: 'r-100000',
      since: '2026-09-05T08:58:40Z',
      summary: 'Fable is rate limited and Anthropic is cooling',
    },
    {
      kind: 'session_blocked',
      session_id: `r-${(0x100000 + 7919).toString(16)}`,
      since: '2026-09-05T08:44:10Z',
      summary: 'The conversation no longer fits any enabled model',
    },
  ],
});

// 4. An ended session, still readable for a while.
write('overview-ended.json', {
  generated_at: AT,
  console_version: '0.1.0',
  sessions: [
    session({
      id: 'r-2b77e0',
      state: 'running',
      model: 'claude-sonnet-5[1m]',
      provider: 'anthropic',
      workdir: 'C:\\Users\\Harsh kamdar\\Desktop\\Opensource\\airlock-relay',
      tokens: 88210,
      url: 'http://127.0.0.1:41880',
    }),
    session({
      id: 'r-8f2c1a',
      state: 'ended',
      model: 'claude-fable-5-1[1m]',
      provider: 'anthropic',
      workdir: 'C:\\Users\\Harsh kamdar\\Desktop\\Opensource\\claudex',
      started: '2026-09-05T06:41:03Z',
      last: '2026-09-05T08:58:40Z',
      tokens: 612340,
      workers: [
        { model: 'gpt-5.6-luna', requests: 14 },
        { model: 'gpt-5.6-sol', requests: 3 },
      ],
      handoffs: 2,
      url: 'http://127.0.0.1:39123',
    }),
  ],
  routes: READY_ROUTES,
  headroom: HEADROOM_KNOWN,
  attention: [],
});

// 5. Everything ready. The calm case, and the one that proves the accent
//    colour never appears when no decision is needed.
write('overview-ready.json', {
  generated_at: AT,
  console_version: '0.1.0',
  sessions: [
    session({
      id: 'r-2b77e0',
      state: 'running',
      model: 'claude-sonnet-5[1m]',
      provider: 'anthropic',
      workdir: 'C:\\Users\\Harsh kamdar\\Desktop\\Opensource\\airlock-relay',
      tokens: 88210,
      url: 'http://127.0.0.1:41880',
    }),
    session({
      id: 'r-c91d44',
      state: 'idle',
      model: 'gpt-5.6-sol',
      provider: 'openai',
      workdir: 'D:\\agentquant',
      window: 400000,
      tokens: 142900,
      last: '2026-09-05T08:31:12Z',
      profile: 'openai-direct',
      url: 'http://127.0.0.1:40217',
    }),
  ],
  routes: READY_ROUTES,
  headroom: [
    { provider: 'anthropic', window: '5h', used_percent: 12, resets_at: '2026-09-05T11:00:00Z', source: 'status_line' },
    { provider: 'openai', window: '5h', used_percent: 8, resets_at: '2026-09-05T12:15:00Z', source: 'status_line' },
  ],
  attention: [],
});

// 6. Headroom is unknown for every provider. The rest stays ordinary contract
//    data so this fixture isolates the one unknown state it is meant to show.
write('overview-unknown.json', {
  generated_at: AT,
  console_version: '0.1.0',
  sessions: [
    session({
      id: 'r-9d0c31',
      state: 'idle',
      model: 'local-coder',
      provider: 'openmodel',
      workdir: 'D:\\agentquant',
      profile: 'openmodel-local',
      tokens: 22400,
      window: 131072,
      last: '2026-09-05T08:31:12Z',
    }),
  ],
  routes: [
    route({ model: 'local-coder', short: 'local-coder', provider: 'openmodel', category: 'unknown', window: null, effort: 'high', status: 'ready', using: ['r-9d0c31'] }),
    route({ model: 'stealth/ox-alpha', short: 'ox-alpha', provider: 'openrouter', category: 'extra', metered: true, window: 1000000 }),
  ],
  headroom: HEADROOM_UNKNOWN,
  attention: [],
});

// 7. The running session, with enough completed requests to prove the quiet
//    rows collapse per model per minute.
const quiet = [];
for (let i = 0; i < 14; i += 1) {
  quiet.push({
    timestamp: `2026-09-05T09:01:${String(10 + i * 3).padStart(2, '0')}Z`,
    provider: 'openai',
    model: 'gpt-5.6-luna',
    status: 200,
    outcome: 'completed',
    duration_ms: 3200 + i * 90,
    usage: { input_tokens: 4100, output_tokens: 380, cache_read_input_tokens: 0 },
  });
}
write('session-r-2b77e0.json', {
  ...session({
    id: 'r-2b77e0',
    state: 'running',
    model: 'claude-sonnet-5[1m]',
    provider: 'anthropic',
    workdir: 'C:\\Users\\Harsh kamdar\\Desktop\\Opensource\\airlock-relay',
    started: '2026-09-05T08:12:50Z',
    last: '2026-09-05T09:02:05Z',
    tokens: 88210,
    workers: [{ model: 'gpt-5.6-luna', requests: 14 }],
    url: 'http://127.0.0.1:41880',
  }),
  routes: READY_ROUTES.map((r) => ({
    ...r,
    fits_context: true,
    sessions_using: r.model === 'claude-sonnet-5[1m]' ? ['r-2b77e0'] : [],
  })),
  cooldowns: [],
  chains: {
    'claude-sonnet-5[1m]': ['claude-fable-5-1[1m]', 'gpt-5.6-sol'],
    'gpt-5.6-luna': ['grok-4.6'],
  },
  usage: [
    { provider: 'anthropic', model: 'claude-sonnet-5[1m]', requests: 61, completed: 61, errors: 0, input_tokens: 8210000, output_tokens: 121000, cache_read_input_tokens: 7900000 },
    { provider: 'openai', model: 'gpt-5.6-luna', requests: 14, completed: 14, errors: 0, input_tokens: 57400, output_tokens: 5320, cache_read_input_tokens: 0 },
  ],
  events: [
    { timestamp: '2026-09-05T08:12:50Z', kind: 'session_model_pinned', model: 'claude-sonnet-5[1m]', provider: 'anthropic' },
    ...quiet,
    { timestamp: '2026-09-05T09:02:05Z', provider: 'anthropic', model: 'claude-sonnet-5[1m]', status: 200, outcome: 'completed', duration_ms: 12400, usage: { input_tokens: 1800, output_tokens: 3100, cache_read_input_tokens: 86000 } },
  ],
  last_handoff: null,
});

// 8. Every event kind the spec names, plus one the console has never seen, so
//    the unknown path is exercised by a fixture and not only by a test.
const ALL_KINDS_EVENTS = [
  { timestamp: '2026-09-05T08:00:00Z', kind: 'session_model_pinned', model: 'claude-fable-5-1[1m]', provider: 'anthropic' },
  { timestamp: '2026-09-05T08:00:01Z', kind: 'router_restarted' },
  { timestamp: '2026-09-05T08:01:00Z', kind: 'model_not_enabled', model: 'claude-haiku-4-5-20251001' },
  { timestamp: '2026-09-05T08:01:05Z', kind: 'background_model_substituted', model: 'gpt-5.6-luna' },
  { timestamp: '2026-09-05T08:02:00Z', kind: 'openrouter_effort_clamped', model: 'stealth/ox-alpha' },
  { timestamp: '2026-09-05T08:02:01Z', kind: 'openrouter_server_tools_stripped', model: 'stealth/ox-alpha' },
  { timestamp: '2026-09-05T08:03:00Z', provider: 'openai', model: 'gpt-5.6-sol', status: 429, outcome: 'rate_limited', duration_ms: 700 },
  { timestamp: '2026-09-05T08:03:00Z', kind: 'rate_limit_failover_attempted', from_model: 'gpt-5.6-sol', to_model: 'gpt-5.6-terra', models_considered: 2 },
  { timestamp: '2026-09-05T08:03:01Z', provider: 'openai', model: 'gpt-5.6-terra', status: 200, outcome: 'completed', duration_ms: 9300, failover_from: 'gpt-5.6-sol' },
  { timestamp: '2026-09-05T08:03:01Z', kind: 'rate_limit_failover_succeeded', from_model: 'gpt-5.6-sol', to_model: 'gpt-5.6-terra' },
  { timestamp: '2026-09-05T08:03:02Z', kind: 'rate_limit_cooldown_skipped', model: 'gpt-5.6-luna', provider: 'openai' },
  { timestamp: '2026-09-05T08:03:03Z', kind: 'rate_limit_provider_cooldown', provider: 'openai', remaining_seconds: 900 },
  { timestamp: '2026-09-05T08:03:04Z', kind: 'anthropic_rate_limit_passthrough', provider: 'anthropic', model: 'claude-fable-5-1[1m]', status: 429 },
  { timestamp: '2026-09-05T08:03:05Z', kind: 'rate_limit_chain_exhausted', model: 'gpt-5.6-sol', models_considered: 4 },
  { timestamp: '2026-09-05T08:04:00Z', kind: 'upstream_context_overflow', provider: 'anthropic', model: 'claude-fable-5-1[1m]', status: 400 },
  { timestamp: '2026-09-05T08:04:01Z', kind: 'failover_overflow_attempted', from_model: 'claude-fable-5-1[1m]', to_model: 'grok-4.6' },
  { timestamp: '2026-09-05T08:04:01Z', provider: 'grok', model: 'grok-4.6', status: 200, outcome: 'completed', duration_ms: 11200, failover_from: 'claude-fable-5-1[1m]' },
  { timestamp: '2026-09-05T08:04:01Z', kind: 'failover_overflow_succeeded', from_model: 'claude-fable-5-1[1m]', to_model: 'grok-4.6' },
  { timestamp: '2026-09-05T08:04:02Z', kind: 'failover_overflow_skipped', from_model: 'claude-fable-5-1[1m]', to_model: 'gpt-5.6-sol', reason: 'context_window' },
  { timestamp: '2026-09-05T08:04:03Z', kind: 'failover_shrink_compacted', to_model: 'gpt-5.6-sol' },
  { timestamp: '2026-09-05T08:04:04Z', kind: 'failover_shrink_truncated', to_model: 'gpt-5.6-sol', reason: 'no_compactor' },
  { timestamp: '2026-09-05T08:04:05Z', kind: 'failover_shrink_failed', to_model: 'gpt-5.6-sol', reason: 'no_shrink_path' },
  { timestamp: '2026-09-05T08:04:06Z', kind: 'overflow_chain_exhausted', model: 'claude-fable-5-1[1m]', models_considered: 3 },
  { timestamp: '2026-09-05T08:05:00Z', kind: 'sanitized_error_substituted', provider: 'grok', model: 'grok-4.6', status: 502 },
  { timestamp: '2026-09-05T08:06:00Z', kind: 'router_reheated_the_kettle', model: 'grok-4.6' },
  { timestamp: '2026-09-05T08:07:00Z', provider: 'openai', model: 'gpt-5.6-terra', status: 200, outcome: 'completed', duration_ms: 8400, failover_from: 'gpt-5.6-sol' },
  { timestamp: '2026-09-05T08:07:10Z', provider: 'openai', model: 'gpt-5.6-terra', status: 500, outcome: 'error', duration_ms: 1200 },
];
const allKindsSession = session({
  id: 'r-a11c0d',
  state: 'blocked',
  blocked_reason: 'chain_exhausted',
  model: 'claude-fable-5-1[1m]',
  provider: 'anthropic',
  workdir: 'D:\\work\\every-event',
  started: '2026-09-05T08:00:00Z',
  last: '2026-09-05T08:07:10Z',
  tokens: 940000,
  handoffs: 3,
  workers: [{ model: 'gpt-5.6-terra', requests: 2 }],
});
write('overview-allkinds.json', {
  generated_at: AT,
  console_version: '0.1.0',
  sessions: [allKindsSession],
  routes: base.routes,
  headroom: HEADROOM_KNOWN,
  attention: [
    {
      kind: 'session_blocked',
      session_id: 'r-a11c0d',
      since: '2026-09-05T08:07:10Z',
      summary: 'Every route has been tried and none is both free and large enough',
    },
  ],
});
write('session-r-a11c0d.json', {
  ...allKindsSession,
  routes: base.routes.map((r) => ({ ...r, fits_context: r.context_window == null ? null : r.context_window >= 940000 })),
  cooldowns: [
    { scope: 'provider', provider: 'anthropic', remaining_seconds: 2890, until: '2026-09-05T09:50:22Z' },
  ],
  chains: {
    'claude-fable-5-1[1m]': ['claude-opus-5[1m]', 'gpt-5.6-sol', 'grok-4.6'],
  },
  usage: [],
  events: ALL_KINDS_EVENTS,
  last_handoff: { at: '2026-09-05T08:03:00Z', from_model: 'gpt-5.6-sol', to_model: 'gpt-5.6-terra', reason: 'rate_limit' },
});
