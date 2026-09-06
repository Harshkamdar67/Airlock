// Every event kind the spec names must produce a sentence a person would say.
// The assertions are on the flattened text, so a wording change is a visible
// diff rather than a silent one.

import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  buildTimeline,
  renderEvent,
  sentenceText,
  type SentenceContext,
} from '../src/lib/sentences';
import { shortNameLookup } from '../src/lib/names';
import type { AirEvent, SessionDetail } from '../src/types';

const FIXTURES = resolve(__dirname, '..', 'fixtures');
const load = <T,>(name: string): T => JSON.parse(readFileSync(resolve(FIXTURES, name), 'utf8')) as T;

const detail = load<SessionDetail>('session-r-8f2c1a.json');

const ctx: SentenceContext = {
  short: shortNameLookup(detail.routes),
  windowOf: (model) => detail.routes.find((r) => r.model === model)?.context_window ?? null,
};

const say = (ev: AirEvent) => sentenceText(renderEvent(ev, ctx));
const at = '2026-09-05T08:00:00Z';

describe('event to sentence, every kind the spec names', () => {
  it('rate_limit_failover_attempted, in the router shape', () => {
    expect(
      say({
        timestamp: at,
        kind: 'rate_limit_failover_attempted',
        from_model: 'gpt-5.6-sol',
        to_model: 'gpt-5.6-terra',
        models_considered: 2,
      }),
    ).toBe('Sol was rate limited. The router handed off to Terra after considering 2 routes.');
  });

  it('rate_limit_failover_attempted, in the fixture shape', () => {
    expect(
      say({
        timestamp: at,
        kind: 'rate_limit_failover_attempted',
        model: 'claude-opus-5[1m]',
        provider: 'anthropic',
        failover_from: 'claude-fable-5-1[1m]',
        models_considered: 3,
      }),
    ).toBe('Fable was rate limited. The router handed off to Opus after considering 3 routes.');
  });

  it('rate_limit_failover_attempted, one route considered, reads singular', () => {
    expect(
      say({ timestamp: at, kind: 'rate_limit_failover_attempted', from_model: 'gpt-5.6-sol', to_model: 'gpt-5.6-terra', models_considered: 1 }),
    ).toContain('after considering 1 route.');
  });

  it('rate_limit_failover_succeeded preserves from, to, and the hop', () => {
    const s = renderEvent(
      {
        timestamp: at,
        kind: 'rate_limit_failover_succeeded',
        from_model: 'gpt-5.6-sol',
        to_model: 'gpt-5.6-terra',
      },
      ctx,
    );
    expect(sentenceText(s)).toBe('Terra took over from Sol after a rate limit.');
    expect(s.hop).toEqual({ from: 'sol', to: 'terra' });
  });

  it('rate_limit_cooldown_skipped', () => {
    expect(
      say({ timestamp: at, kind: 'rate_limit_cooldown_skipped', model: 'claude-sonnet-5[1m]', provider: 'anthropic' }),
    ).toBe('Sonnet was skipped without a request; it is already cooling.');
  });

  it('rate_limit_provider_cooldown with a remaining time', () => {
    expect(
      say({ timestamp: at, kind: 'rate_limit_provider_cooldown', provider: 'anthropic', remaining_seconds: 2920 }),
    ).toBe('Anthropic is cooling for 49 minutes.');
  });

  it('rate_limit_provider_cooldown without one', () => {
    expect(say({ timestamp: at, kind: 'rate_limit_provider_cooldown', provider: 'grok' })).toBe(
      'Grok is cooling, so the rest of its models were skipped.',
    );
  });

  it('rate_limit_chain_exhausted', () => {
    const s = renderEvent(
      { timestamp: at, kind: 'rate_limit_chain_exhausted', model: 'claude-fable-5-1[1m]', models_considered: 4 },
      ctx,
    );
    expect(sentenceText(s)).toBe('Fable’s chain is exhausted after 4 routes.');
    expect(s.tone).toBe('attn');
    expect(s.note).toMatch(/waiting for a route to open/);
  });

  it('failover_overflow_attempted', () => {
    expect(
      say({ timestamp: at, kind: 'failover_overflow_attempted', from_model: 'claude-fable-5-1[1m]', to_model: 'grok-4.6' }),
    ).toBe('The conversation did not fit Fable, so the router moved it to Grok.');
  });

  it('failover_overflow_succeeded preserves from, to, and the hop', () => {
    const s = renderEvent(
      {
        timestamp: at,
        kind: 'failover_overflow_succeeded',
        from_model: 'claude-fable-5-1[1m]',
        to_model: 'grok-4.6',
      },
      ctx,
    );
    expect(sentenceText(s)).toBe(
      'Grok took over from Fable after the conversation was too large.',
    );
    expect(s.hop).toEqual({ from: 'fable', to: 'grok' });
  });

  it('failover_overflow_skipped names the window it does not fit', () => {
    expect(
      say({ timestamp: at, kind: 'failover_overflow_skipped', model: 'gpt-5.6-sol', provider: 'openai', failover_from: 'claude-fable-5-1[1m]', reason: 'context_window' }),
    ).toBe('Sol was skipped because the conversation does not fit its 400k window.');
  });

  it('failover_overflow_skipped without a known window', () => {
    expect(
      say({ timestamp: at, kind: 'failover_overflow_skipped', to_model: 'someone/else', reason: 'context_window' }),
    ).toBe('Else was skipped because the conversation does not fit its window.');
  });

  it('failover_shrink_compacted, using the contract’s to_model field', () => {
    expect(say({ timestamp: at, kind: 'failover_shrink_compacted', to_model: 'gpt-5.6-sol' })).toBe(
      'The conversation was compacted to fit Sol.',
    );
  });

  it('failover_shrink_truncated explains what was dropped', () => {
    const s = renderEvent({ timestamp: at, kind: 'failover_shrink_truncated', to_model: 'gpt-5.6-sol', reason: 'no_compactor' }, ctx);
    expect(sentenceText(s)).toBe('The conversation was shortened to fit Sol.');
    expect(s.note).toBe('No compactor model was available, so the oldest turns were dropped.');
  });

  it('failover_shrink_truncated, tail only', () => {
    expect(
      renderEvent({ timestamp: at, kind: 'failover_shrink_truncated', to_model: 'gpt-5.6-sol', reason: 'tail_only' }, ctx).note,
    ).toBe('Only the most recent turns were kept.');
  });

  it('failover_shrink_failed', () => {
    const s = renderEvent({ timestamp: at, kind: 'failover_shrink_failed', to_model: 'gpt-5.6-sol', reason: 'no_shrink_path' }, ctx);
    expect(sentenceText(s)).toBe('The conversation could not be shrunk to fit Sol.');
    expect(s.tone).toBe('attn');
  });

  it('overflow_chain_exhausted', () => {
    expect(
      say({ timestamp: at, kind: 'overflow_chain_exhausted', model: 'claude-fable-5-1[1m]', models_considered: 3 }),
    ).toBe('Fable ran out of routes large enough for the conversation after 3 routes.');
  });

  it('upstream_context_overflow', () => {
    expect(
      say({ timestamp: at, kind: 'upstream_context_overflow', model: 'claude-fable-5-1[1m]', provider: 'anthropic', status: 400 }),
    ).toBe('Fable rejected the request because the conversation is too large.');
  });

  it('openrouter_effort_clamped', () => {
    expect(say({ timestamp: at, kind: 'openrouter_effort_clamped', model: 'stealth/ox-alpha' })).toBe(
      'OpenRouter clamped the effort level for Ox-alpha.',
    );
  });

  it('openrouter_server_tools_stripped', () => {
    expect(say({ timestamp: at, kind: 'openrouter_server_tools_stripped', model: 'stealth/ox-alpha' })).toBe(
      'Server side tools were removed from a request to Ox-alpha, which OpenRouter does not run.',
    );
  });

  it('sanitized_error_substituted never shows the provider body', () => {
    const text = say({ timestamp: at, kind: 'sanitized_error_substituted', provider: 'grok', model: 'grok-4.6', status: 502 });
    expect(text).toBe(
      'Grok returned a 502 error; the router replaced the provider’s message with a safe one.',
    );
  });

  it('background_model_substituted', () => {
    expect(say({ timestamp: at, kind: 'background_model_substituted', model: 'gpt-5.6-luna' })).toBe(
      'A background step asked for a model this session does not have, so the router used Luna instead.',
    );
  });

  it('anthropic_rate_limit_passthrough', () => {
    expect(
      say({ timestamp: at, kind: 'anthropic_rate_limit_passthrough', provider: 'anthropic', model: 'claude-fable-5-1[1m]', status: 429 }),
    ).toBe('Anthropic’s rate limit was passed straight through to the client for Fable.');
  });

  it('model_not_enabled', () => {
    const s = renderEvent({ timestamp: at, kind: 'model_not_enabled', model: 'claude-haiku-4-5-20251001' }, ctx);
    expect(sentenceText(s)).toBe('A request asked for Haiku, which is not enabled for this session.');
    expect(s.tone).toBe('attn');
  });

  it('session_root_selected', () => {
    expect(
      say({ timestamp: at, kind: 'session_root_selected', model: 'claude-fable-5-1[1m]', provider: 'anthropic' }),
    ).toBe('The session started on Fable.');
  });

  it('session_model_pinned', () => {
    expect(
      say({ timestamp: at, kind: 'session_model_pinned', model: 'claude-fable-5-1[1m]', provider: 'anthropic' }),
    ).toBe('The session is pinned to Fable on Anthropic.');
  });

  it('session_model_unpinned', () => {
    expect(
      say({ timestamp: at, kind: 'session_model_unpinned', model: 'grok-4.6', provider: 'grok' }),
    ).toBe('The session returned to its root model from Grok.');
  });

  it('router_restarted', () => {
    expect(say({ timestamp: at, kind: 'router_restarted' })).toBe('The router restarted.');
  });

  it('an unknown kind is shown, never hidden', () => {
    const s = renderEvent({ timestamp: at, kind: 'router_reheated_the_kettle', model: 'grok-4.6' }, ctx);
    expect(sentenceText(s)).toBe('The router recorded router_reheated_the_kettle for Grok.');
    expect(s.pieces.some((p) => p.t === 'raw' && p.v === 'router_reheated_the_kettle')).toBe(true);
  });

  it('every kind the spec names has wording of its own', () => {
    const KINDS = [
      'rate_limit_failover_attempted', 'rate_limit_failover_succeeded',
      'rate_limit_cooldown_skipped', 'rate_limit_provider_cooldown',
      'rate_limit_chain_exhausted', 'failover_overflow_attempted',
      'failover_overflow_succeeded', 'failover_overflow_skipped',
      'failover_shrink_compacted', 'failover_shrink_truncated', 'failover_shrink_failed',
      'overflow_chain_exhausted', 'upstream_context_overflow', 'openrouter_effort_clamped',
      'openrouter_server_tools_stripped', 'sanitized_error_substituted', 'background_model_substituted',
      'anthropic_rate_limit_passthrough', 'model_not_enabled', 'session_root_selected', 'session_model_pinned', 'session_model_unpinned', 'router_restarted',
    ];
    for (const kind of KINDS) {
      const text = say({ timestamp: at, kind, model: 'gpt-5.6-sol', provider: 'openai' });
      expect(text, kind).not.toContain('The router recorded');
      expect(text, kind).not.toContain(kind);
      expect(text.endsWith('.'), kind).toBe(true);
    }
  });
});

describe('plain request events', () => {
  it('a completed request is quiet', () => {
    expect(
      renderEvent({ timestamp: at, model: 'gpt-5.6-luna', provider: 'openai', status: 200, outcome: 'completed' }, ctx).tone,
    ).toBe('quiet');
  });

  it('a completed request that took over from another is spoken', () => {
    const s = renderEvent(
      { timestamp: at, model: 'gpt-5.6-terra', provider: 'openai', status: 200, outcome: 'completed', duration_ms: 14020, failover_from: 'gpt-5.6-sol' },
      ctx,
    );
    expect(s.tone).toBe('normal');
    expect(sentenceText(s)).toBe('Terra picked up from Sol and completed in 14.0 seconds.');
  });

  it('a rate limited request is spoken', () => {
    expect(say({ timestamp: at, model: 'gpt-5.6-sol', provider: 'openai', status: 429, outcome: 'rate_limited' })).toBe(
      'Sol was rate limited.',
    );
  });

  it('a rate limited request after a handoff says so', () => {
    expect(
      say({ timestamp: at, model: 'claude-opus-5[1m]', provider: 'anthropic', status: 429, outcome: 'rate_limited', failover_from: 'claude-fable-5-1[1m]' }),
    ).toBe('Opus was rate limited too, after taking over from Fable.');
  });

  it('any other failure reports its status and nothing from the provider', () => {
    expect(say({ timestamp: at, model: 'gpt-5.6-terra', provider: 'openai', status: 500, outcome: 'error' })).toBe(
      'Terra failed with status 500.',
    );
  });
});

describe('timeline assembly', () => {
  const groups = buildTimeline(detail.events, ctx);

  it('groups by minute, newest first', () => {
    expect(groups.map((g) => g.label)).toEqual(['08:58', '08:57', '08:51', '08:44', '08:41']);
  });

  it('absorbs the 429 that a same moment handoff already explains', () => {
    const eight51 = groups.find((g) => g.label === '08:51')!;
    const texts = eight51.rows.map((r) => (r.type === 'spoken' ? sentenceText(r.sentence) : r.summary));
    expect(texts).toEqual([
      'Terra picked up from Sol and completed in 14.0 seconds.',
      'Sol was rate limited. The router handed off to Terra after considering 2 routes.',
    ]);
  });

  it('reads the newest event first inside a minute, cause below consequence', () => {
    const eight57 = groups.find((g) => g.label === '08:57')!;
    expect(eight57.rows.map((r) => (r.type === 'spoken' ? sentenceText(r.sentence) : r.summary))).toEqual([
      'Sonnet was skipped without a request; it is already cooling.',
      'Anthropic is cooling for 49 minutes.',
      'Opus was rate limited too, after taking over from Fable.',
      'Fable was rate limited. The router handed off to Opus after considering 3 routes.',
    ]);
  });

  it('draws a handoff as from to to', () => {
    const row = groups[1].rows.at(-1);
    expect(row?.type === 'spoken' && row.sentence.hop).toEqual({ from: 'fable', to: 'opus' });
  });

  it('collapses completed requests per model per minute', () => {
    const running = load<SessionDetail>('session-r-2b77e0.json');
    const rctx: SentenceContext = {
      short: shortNameLookup(running.routes),
      windowOf: () => null,
    };
    const summaries = buildTimeline(running.events, rctx)
      .flatMap((g) => g.rows)
      .filter((r) => r.type === 'quiet')
      .map((r) => (r as { summary: string }).summary);
    expect(summaries).toContain('14 requests to Luna, all completed');
    expect(summaries).toContain('1 request to Sonnet, completed');
  });

  it('filters by kind group', () => {
    const handoffs = buildTimeline(detail.events, ctx, { group: 'handoffs' });
    const kinds = handoffs.flatMap((g) => g.rows).map((r) => (r.type === 'spoken' ? r.sentence.kind : 'quiet'));
    expect(kinds).toEqual(['rate_limit_chain_exhausted', 'rate_limit_failover_attempted', 'rate_limit_failover_attempted']);
  });

  it('filters by model, including the model a handoff came from', () => {
    const onlySonnet = buildTimeline(detail.events, ctx, { model: 'claude-sonnet-5[1m]' });
    expect(onlySonnet.flatMap((g) => g.rows)).toHaveLength(1);
  });

  it('absorbs matched success diagnostics into completed failover requests, but keeps unmatched ones', () => {
    const ts = '2026-09-05T08:30:00Z';
    const events: AirEvent[] = [
      {
        timestamp: ts,
        kind: 'rate_limit_failover_succeeded',
        from_model: 'gpt-5.6-sol',
        to_model: 'gpt-5.6-terra',
      },
      {
        timestamp: '2026-09-05T08:30:02Z',
        provider: 'openai',
        model: 'gpt-5.6-terra',
        failover_from: 'gpt-5.6-sol',
        status: 200,
        outcome: 'completed',
        duration_ms: 1000,
      },
      {
        // No completed request names Luna, so this one must still be shown.
        timestamp: '2026-09-05T08:30:40Z',
        kind: 'rate_limit_failover_succeeded',
        from_model: 'gpt-5.6-sol',
        to_model: 'gpt-5.6-luna',
      },
      {
        timestamp: '2026-09-05T08:31:00Z',
        kind: 'failover_overflow_succeeded',
        from_model: 'claude-fable-5-1[1m]',
        to_model: 'grok-4.6',
      },
      {
        timestamp: '2026-09-05T08:31:02Z',
        provider: 'grok',
        model: 'grok-4.6',
        failover_from: 'claude-fable-5-1[1m]',
        status: 200,
        outcome: 'completed',
      },
      {
        timestamp: '2026-09-05T08:31:40Z',
        kind: 'failover_overflow_succeeded',
        from_model: 'claude-fable-5-1[1m]',
        to_model: 'stealth/ox-alpha',
      },
    ];
    const rows = buildTimeline(events, ctx).flatMap((g) => g.rows);
    const texts = rows.map((r) =>
      r.type === 'spoken' ? sentenceText(r.sentence) : r.summary,
    );
    expect(texts.filter((text) => text.includes('Terra picked up'))).toHaveLength(1);
    expect(texts).not.toContain('Terra took over from Sol after a rate limit.');
    expect(texts).toContain('Luna took over from Sol after a rate limit.');
    expect(texts.filter((text) => text.includes('Grok picked up from Fable after the conversation was too large'))).toHaveLength(1);
    expect(texts).not.toContain(
      'Grok took over from Fable after the conversation was too large.',
    );
    expect(texts).toContain(
      'Ox-alpha took over from Fable after the conversation was too large.',
    );
    // Same-minute output stays newest first.
    expect(texts.indexOf('Luna took over from Sol after a rate limit.')).toBeLessThan(
      texts.indexOf('Terra picked up from Sol after a rate limit and completed in 1.0 seconds.'),
    );
  });

  it('keeps a request and its diagnostic together across a second boundary', () => {
    const straddling: AirEvent[] = [
      { timestamp: '2026-09-05T08:58:50Z', kind: 'rate_limit_failover_attempted', from_model: 'gpt-5.6-sol', to_model: 'gpt-5.6-terra', models_considered: 2 },
      { timestamp: '2026-09-05T08:58:51Z', provider: 'openai', model: 'gpt-5.6-sol', status: 429, outcome: 'rate_limited' },
      { timestamp: '2026-09-05T08:58:59Z', kind: 'rate_limit_failover_succeeded', from_model: 'gpt-5.6-sol', to_model: 'gpt-5.6-terra' },
      { timestamp: '2026-09-05T08:59:00Z', provider: 'openai', model: 'gpt-5.6-terra', failover_from: 'gpt-5.6-sol', status: 200, outcome: 'completed', duration_ms: 9300 },
    ];
    const texts = buildTimeline(straddling, ctx)
      .flatMap((g) => g.rows)
      .map((row) => (row.type === 'spoken' ? sentenceText(row.sentence) : row.summary));
    expect(texts).toEqual([
      'Terra picked up from Sol after a rate limit and completed in 9.3 seconds.',
      'Sol was rate limited. The router handed off to Terra after considering 2 routes.',
    ]);
  });

  it('renders every event in the all kinds fixture without throwing', () => {
    const all = load<SessionDetail>('session-r-a11c0d.json');
    const actx: SentenceContext = { short: shortNameLookup(all.routes), windowOf: () => 400000 };
    const rows = buildTimeline(all.events, actx).flatMap((g) => g.rows);
    expect(rows.length).toBeGreaterThan(15);
    for (const row of rows) {
      const text = row.type === 'quiet' ? row.summary : sentenceText(row.sentence);
      expect(text.length).toBeGreaterThan(3);
    }
  });
});
