// The derived verdict, and the fixtures it is derived from.

import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { resolveBlocked } from '../src/lib/resolution';
import { buildReport } from '../src/lib/report';
import { markdownCode, markdownText } from '../src/lib/markdown';
import { deriveShortName, joinWords, projectFromWorkdir } from '../src/lib/names';
import type { Overview, SessionDetail } from '../src/types';

const FIXTURES = resolve(__dirname, '..', 'fixtures');
const load = <T,>(name: string): T => JSON.parse(readFileSync(resolve(FIXTURES, name), 'utf8')) as T;
const detail = load<SessionDetail>('session-r-8f2c1a.json');

describe('what unblocks this', () => {
  const r = resolveBlocked(detail)!;

  it('leads with why it stopped and when it stops being stopped', () => {
    expect(r.lead).toBe('Fable’s handoff chain had nothing left to try. Anthropic frees up in 48 minutes.');
  });

  it('names the route that is ready and large enough, first', () => {
    expect(r.options[0].tone).toBe('ok');
    expect(r.options[0].text).toBe('ox-alpha is ready now and fits this conversation.');
    expect(r.options[0].detail).toBe('openrouter, extra usage');
  });

  it('explains the ones that look available but are not', () => {
    expect(r.options[1].text).toBe(
      'sol has room, but 612k does not fit its 400k window.',
    );
  });

  it('says nothing at all for a session that is not blocked', () => {
    expect(resolveBlocked({ ...detail, state: 'running' })).toBeNull();
    expect(resolveBlocked({ ...detail, state: 'idle' })).toBeNull();
  });

  it('is honest when nothing at all is ready', () => {
    const nothing = resolveBlocked({
      ...detail,
      routes: detail.routes.map((route) => ({ ...route, status: 'cooling' as const })),
    })!;
    expect(nothing.options[0].text).toBe('Nothing is ready. The first route opens in 48 minutes.');
  });

  it('does not promise a time it was never told', () => {
    const noTimes = resolveBlocked({
      ...detail,
      cooldowns: [],
      routes: detail.routes.map((route) => ({
        ...route,
        status: 'cooling' as const,
        cooldown_remaining_seconds: null,
      })),
    })!;
    expect(noTimes.options[0].text).toBe(
      'Nothing is ready, and no route has told the router when it will be.',
    );
    expect(noTimes.lead).not.toMatch(/frees up/);
  });

  it('only recommends routes in the active model’s frozen chain', () => {
    const allKinds = load<SessionDetail>('session-r-a11c0d.json');
    const result = resolveBlocked(allKinds)!;
    expect(result.options.map((o) => o.text).join(' ')).not.toContain('ox-alpha');
    expect(result.options.map((o) => o.text).join(' ')).not.toContain('terra');
  });

  it('keeps an unknown fit unknown instead of recommending or rejecting it', () => {
    const unknown = resolveBlocked({
      ...detail,
      routes: detail.routes.map((route) =>
        route.model === 'stealth/ox-alpha' ? { ...route, fits_context: null } : route,
      ),
    })!;
    expect(unknown.options.some((o) => o.text.includes('cannot tell whether this conversation fits'))).toBe(true);
    expect(unknown.options.find((o) => o.text.includes('cannot tell'))?.tone).toBe('unknown');
    expect(unknown.options.some((o) => /ox-alpha is ready now and fits/.test(o.text))).toBe(false);
  });

  it('uses only the active model provider cooldown for provider-blocked wording', () => {
    const unrelatedOnly = resolveBlocked({
      ...detail,
      blocked_reason: 'provider_cooldown',
      cooldowns: [
        { scope: 'provider', provider: 'openai', remaining_seconds: 300 },
      ],
    })!;
    expect(unrelatedOnly.lead).toBe(
      'Fable is blocked, but the router has not reported a cooldown for Anthropic.',
    );

    const both = resolveBlocked({
      ...detail,
      blocked_reason: 'provider_cooldown',
      cooldowns: [
        { scope: 'provider', provider: 'openai', remaining_seconds: 60 },
        { scope: 'provider', provider: 'anthropic', remaining_seconds: 600 },
      ],
    })!;
    expect(both.lead).toBe('Anthropic is cooling. It frees up in 10 minutes.');
  });

  it('words a context overflow as a size problem, not a limit problem', () => {
    // Fable's chain still holds ox-alpha, which fits, so claiming nothing is
    // large enough would contradict the inspector three inches away.
    const overflow = resolveBlocked({ ...detail, blocked_reason: 'context_overflow' })!;
    expect(overflow.lead).toBe(
      'The conversation is 612k tokens and the route it was on could not take it.',
    );

    const nothingFits = resolveBlocked({
      ...detail,
      blocked_reason: 'context_overflow',
      routes: detail.routes.map((route) => ({ ...route, fits_context: false })),
    })!;
    expect(nothingFits.lead).toBe(
      'The conversation is 612k tokens and no route in Fable’s chain has a window that large.',
    );

    // With no declared chain the router derives one, so the wording must not
    // blame a chain the session does not have.
    const noChain = resolveBlocked({
      ...detail,
      blocked_reason: 'context_overflow',
      chains: {},
      routes: detail.routes.map((route) => ({ ...route, fits_context: false })),
    })!;
    expect(noChain.lead).toBe(
      'The conversation is 612k tokens and no enabled route has a window that large.',
    );
  });
});

describe('names', () => {
  it('shortens model ids the way the routes list does', () => {
    expect(deriveShortName('claude-fable-5-1[1m]')).toBe('fable');
    expect(deriveShortName('claude-haiku-4-5-20251001')).toBe('haiku');
    expect(deriveShortName('gpt-5.6-sol')).toBe('sol');
    expect(deriveShortName('grok-4.6')).toBe('grok');
    expect(deriveShortName('grok-composer-2.5-fast')).toBe('composer');
    expect(deriveShortName('moonshotai/kimi-k3')).toBe('kimi-k3');
    expect(deriveShortName('stealth/ox-alpha')).toBe('ox-alpha');
  });

  it('joins a list the way it is spoken', () => {
    expect(joinWords(['sol'])).toBe('sol');
    expect(joinWords(['sol', 'terra'])).toBe('sol and terra');
    expect(joinWords(['sol', 'terra', 'luna'])).toBe('sol, terra and luna');
  });

  it('takes the project from either separator', () => {
    expect(projectFromWorkdir('C:\\Users\\h\\Desktop\\claudex')).toBe('claudex');
    expect(projectFromWorkdir('/home/h/src/claudex/')).toBe('claudex');
  });
});

describe('the Markdown report', () => {
  const md = buildReport(detail, Date.parse('2026-09-05T09:02:12Z'));

  it('opens with the session and its state', () => {
    expect(md).toContain('# claudex, Airlock session `r-8f2c1a`');
    expect(md).toContain('- State: Blocked, chain exhausted');
  });

  it('carries the verdict and the timeline', () => {
    expect(md).toContain('## What unblocks this');
    expect(md).toContain('Fable’s chain is exhausted after 4 routes.');
    expect(md).toContain('(`fable` -> `opus`)');
  });

  it('labels the active model with its own provider and keeps null fit unknown', () => {
    const report = buildReport(
      {
        ...detail,
        active_model: 'gpt-5.6-sol',
        routes: detail.routes.map((route) => ({
          ...route,
          fits_context: route.model === 'gpt-5.6-sol' ? null : route.fits_context,
        })),
      },
      Date.parse('2026-09-05T09:02:12Z'),
    );
    expect(report).toContain('Active model: `sol` on openai');
    expect(report).toContain('`sol` (`gpt-5.6-sol`, openai, included)');
    expect(report).toContain('fit unknown');
    expect(report).not.toMatch(/sol .*does not fit/);
  });

  const HOSTILE = [
    'C:\\tmp\\![x](https://evil/pixel)',
    '# heading',
    '- list',
    '> quote',
    '```code```',
    '<link>',
  ].join('\n');

  it('neutralises every injection route in an untrusted field', () => {
    const poisoned: SessionDetail = {
      ...detail,
      project: HOSTILE,
      id: HOSTILE,
      workdir: HOSTILE,
      profile: HOSTILE,
      active_model: HOSTILE,
      root_model: HOSTILE,
      root_provider: HOSTILE,
      routes: [
        {
          ...detail.routes[0],
          model: HOSTILE,
          short_name: HOSTILE,
          provider: HOSTILE,
          category: 'unknown',
          fits_context: null,
        },
      ],
      chains: { [HOSTILE]: [HOSTILE] },
      workers: [{ model: HOSTILE, requests: 1 }],
      events: [
        {
          timestamp: '2026-09-05T08:00:00Z',
          kind: HOSTILE,
          model: HOSTILE,
          provider: HOSTILE,
        },
      ],
    };
    const report = buildReport(poisoned, Date.parse('2026-09-05T09:02:12Z'));
    // Inside a code span the text is inert, so the active-Markdown assertions
    // are made against the report with its code spans removed.
    const active = report.replace(/`[^`]*`/g, '');

    expect(active).not.toContain('![x](https://evil/pixel)');
    expect(active).not.toContain('](https://evil/pixel)');
    expect(report).not.toContain('\n# heading');
    expect(report).not.toContain('\n- list');
    expect(report).not.toContain('\n> quote');
    expect(active).not.toContain('```code```');
    expect(active).not.toContain('<link>');

    // Every structural line is one the report itself authored: the single
    // title on line 0, or a section heading. A hostile field cannot add one.
    report
      .split('\n')
      .forEach((line, index) => {
        if (!/^(?:#|>|\* |\+ |\d+[.)] )/.test(line)) return;
        const authored =
          index === 0 ||
          /^## (?:What unblocks this|Routes|Chain for|Timeline, newest first|Workers)/.test(line) ||
          /^### [0-9:]+$/.test(line);
        expect(authored, line.slice(0, 70)).toBe(true);
      });
  });

  it('keeps identifiers readable in code spans instead of escaping them', () => {
    const report = buildReport(detail, Date.parse('2026-09-05T09:02:12Z'));
    expect(report).toContain('`claude-fable-5-1[1m]`');
    expect(report).not.toContain('claude\\-fable');
    expect(report).toContain('`C:\\Users\\Harsh kamdar\\Desktop\\Opensource\\claudex`');
    expect(markdownCode('2026-09-05T13:43:10Z')).toBe('`2026-09-05T13:43:10Z`');
    expect(markdownCode('claude-fable-5-1[1m]')).toBe('`claude-fable-5-1[1m]`');
    // A backtick cannot escape its own code span.
    expect(markdownCode('a`b')).toBe('`ab`');
  });

  it('escapes prose only where Markdown is actually active', () => {
    expect(markdownText('Sol was rate limited after 2 routes.')).toBe(
      'Sol was rate limited after 2 routes.',
    );
    expect(markdownText('gpt-5.6-sol handed off to terra')).toBe(
      'gpt-5.6-sol handed off to terra',
    );
    expect(markdownText('a [link](http://x) and *emphasis*')).toBe(
      'a \\[link\\]\\(http://x\\) and \\*emphasis\\*',
    );
    expect(markdownText('- not a bullet')).toBe('\\- not a bullet');
    expect(markdownText('1. not a list')).toBe('\\1. not a list');
    expect(markdownText('+ not a bullet')).toBe('\\+ not a bullet');
    expect(markdownText('one\ntwo')).toBe('one two');
  });

  it('carries nothing a router was told to keep to itself', () => {
    expect(md).not.toMatch(/sk-|Bearer|api[_-]?key/i);
    expect(md).not.toContain(detail.url);
  });
});

describe('fixtures', () => {
  const files = readdirSync(FIXTURES).filter((f) => f.endsWith('.json'));
  const apiFixtures = files.filter(
    (f) => !f.startsWith('proposals-') && !f.startsWith('chains-'),
  );

  it('are all valid JSON with the fields the page reads', () => {
    for (const file of apiFixtures) {
      const data = load<Overview & SessionDetail>(file);
      if (file.startsWith('overview')) {
        expect(Array.isArray(data.sessions), file).toBe(true);
        expect(Array.isArray(data.routes), file).toBe(true);
        expect(Array.isArray(data.headroom), file).toBe(true);
        expect(Array.isArray(data.attention), file).toBe(true);
      } else {
        expect(typeof data.id, file).toBe('string');
        expect(Array.isArray(data.events), file).toBe(true);
      }
    }
  });

  it('cover the states the page has to render', () => {
    expect(load<Overview>('overview-empty.json').sessions).toHaveLength(0);
    expect(load<Overview>('overview-one.json').sessions).toHaveLength(1);
    expect(load<Overview>('overview-twelve.json').sessions).toHaveLength(12);
    expect(load<Overview>('overview-ended.json').sessions.some((s) => s.state === 'ended')).toBe(true);
    expect(load<Overview>('overview-ready.json').routes.every((r) => r.status === 'ready')).toBe(true);
    expect(load<Overview>('overview-unknown.json').headroom.every((h) => h.used_percent === null)).toBe(true);
  });

  it('all-kinds integration fixture uses normalized targets and success diagnostics', () => {
    const all = load<SessionDetail>('session-r-a11c0d.json');
    const kinds = all.events.map((event) => event.kind);
    expect(kinds).toContain('rate_limit_failover_succeeded');
    expect(kinds).toContain('failover_overflow_succeeded');
    for (const kind of [
      'failover_shrink_compacted',
      'failover_shrink_truncated',
      'failover_shrink_failed',
    ]) {
      const event = all.events.find((candidate) => candidate.kind === kind);
      expect(event?.to_model, kind).toBe('gpt-5.6-sol');
    }
  });

  it('never carry a value that looks like a credential', () => {
    for (const file of files) {
      const text = readFileSync(resolve(FIXTURES, file), 'utf8');
      expect(text, file).not.toMatch(/sk-[A-Za-z0-9]{8}/);
      expect(text, file).not.toMatch(/authorization/i);
    }
  });
});
