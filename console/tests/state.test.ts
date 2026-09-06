import { describe, expect, it } from 'vitest';
import {
  filtersForDetail,
  filtersForSessionSwitch,
  mayApplyInitialOverview,
} from '../src/lib/state';
import { hasWorkdir, sessionSubtitle, sessionTitle } from '../src/lib/names';
import { sharedUnknowns, sharedUnknownNotice } from '../src/lib/vocab';

describe('overview ordering', () => {
  it('does not let the slow initial GET overwrite a newer SSE snapshot', () => {
    expect(mayApplyInitialOverview(true, 0, 0)).toBe(true);
    expect(mayApplyInitialOverview(true, 0, 1)).toBe(false);
    expect(mayApplyInitialOverview(false, 0, 0)).toBe(false);
  });
});

describe('session-local timeline filters', () => {
  it('clears the model whenever the selected session changes', () => {
    expect(filtersForSessionSwitch({ group: 'overflow', model: 'gpt-5.6-sol' })).toEqual({
      group: 'overflow',
      model: null,
    });
  });

  it('keeps a historical event-only model through detail refresh', () => {
    expect(
      filtersForDetail(
        { group: 'other', model: 'claude-haiku-4-5-20251001' },
        ['claude-fable-5-1[1m]', 'claude-haiku-4-5-20251001'],
      ),
    ).toEqual({ group: 'other', model: 'claude-haiku-4-5-20251001' });
  });

  it('clears a model that has no event in the current detail', () => {
    expect(
      filtersForDetail(
        { group: 'all', model: 'claude-haiku-4-5-20251001' },
        ['claude-sonnet-5[1m]', 'gpt-5.6-luna'],
      ),
    ).toEqual({ group: 'all', model: null });
  });
});

describe('a router that reports no working directory', () => {
  const bare = { id: 'r-e37907abc', project: '', workdir: '' };

  it('names the session from its instance id rather than showing nothing', () => {
    expect(sessionTitle(bare)).toBe('Session e37907');
    expect(sessionTitle({ ...bare, id: 'e37907abc' })).toBe('Session e37907');
  });

  it('prefers the project, then the path, then the id', () => {
    expect(sessionTitle({ ...bare, project: 'claudex' })).toBe('claudex');
    expect(sessionTitle({ ...bare, workdir: String.raw`D:\\work\\agentquant` })).toBe('agentquant');
    expect(sessionTitle({ ...bare, project: '   ' })).toBe('Session e37907');
  });

  it('never returns an empty heading', () => {
    for (const session of [
      bare,
      { ...bare, project: null as unknown as string },
      { ...bare, workdir: null as unknown as string },
    ]) {
      expect(sessionTitle(session).trim().length).toBeGreaterThan(0);
    }
  });

  it('puts the full instance id where the path would be', () => {
    expect(sessionSubtitle(bare)).toBe('No working directory reported. Session r-e37907abc');
    expect(sessionSubtitle({ ...bare, workdir: String.raw`D:\\work\\x` })).toBe(String.raw`D:\\work\\x`);
    expect(hasWorkdir(bare)).toBe(false);
    expect(hasWorkdir({ ...bare, workdir: String.raw`D:\\work\\x` })).toBe(true);
  });
});

describe('route metadata that is unknown everywhere', () => {
  const unknownRoutes = [
    { category: 'unknown', context_window: null, fits_context: null },
    { category: 'unknown', context_window: null, fits_context: null },
  ];

  it('reports a field only when every route lacks it', () => {
    expect(sharedUnknowns(unknownRoutes)).toEqual({ tier: true, window: true, fit: true });
    expect(
      sharedUnknowns([...unknownRoutes, { category: 'included', context_window: 400000, fits_context: true }]),
    ).toEqual({ tier: false, window: false, fit: false });
  });

  it('keeps a per-route unknown when only some routes lack the field', () => {
    const mixed = [
      { category: 'included', context_window: 400000, fits_context: null },
      { category: 'included', context_window: 400000, fits_context: true },
    ];
    expect(sharedUnknowns(mixed)).toEqual({ tier: false, window: false, fit: false });
  });

  it('says it once, naming exactly the fields that are missing', () => {
    expect(sharedUnknownNotice({ tier: true, window: true, fit: true })).toBe(
      "Tier, window and fit are unknown until this session's router is updated.",
    );
    expect(sharedUnknownNotice({ tier: true, window: false, fit: false })).toBe(
      "Tier is unknown until this session's router is updated.",
    );
    expect(sharedUnknownNotice({ tier: false, window: true, fit: true })).toBe(
      "Window and fit are unknown until this session's router is updated.",
    );
    expect(sharedUnknownNotice({ tier: false, window: false, fit: false })).toBeNull();
  });

  it('blames the missing first request, not the router, when the router is current', () => {
    // Seen live: a fresh router speaks the contract but has served no
    // foreground request yet, so fit is pending rather than unsupported.
    expect(sharedUnknownNotice({ tier: false, window: false, fit: true }, true)).toBe(
      'Fit is unknown until this session sends its first request.',
    );
    expect(sharedUnknownNotice({ tier: true, window: false, fit: true }, true)).toBe(
      'Fit is unknown until this session sends its first request.',
    );
    // Tier never arrives with a request; say so without promising an update.
    expect(sharedUnknownNotice({ tier: true, window: false, fit: false }, true)).toBe(
      'Tier is unknown for these routes.',
    );
    expect(sharedUnknownNotice({ tier: false, window: false, fit: false }, true)).toBeNull();
  });

  it('says nothing for an empty route list', () => {
    expect(sharedUnknowns([])).toEqual({ tier: false, window: false, fit: false });
  });
});
