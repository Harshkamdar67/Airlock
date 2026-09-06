import { describe, expect, it } from 'vitest';
import { loadFixtureDetail } from '../mock/plugin';
import { contextFit } from '../src/lib/state';

describe('scenario-scoped mock details', () => {
  it('keeps an ended summary ended when a rich default detail exists', () => {
    const ended = loadFixtureDetail('ended', 'r-8f2c1a');
    expect(ended?.state).toBe('ended');
    expect(ended?.blocked_reason).toBeNull();
    expect(ended?.project).toBe('claudex');
    expect(ended?.last_activity_at).toBe('2026-09-05T08:58:40Z');
  });

  it('never falls back to a detail absent from the selected scenario', () => {
    expect(loadFixtureDetail('one', 'r-8f2c1a')).toBeNull();
  });

  it('treats unknown context or route windows as unknown fit', () => {
    expect(contextFit(null, 1_000_000)).toBeNull();
    expect(contextFit(200_000, null)).toBeNull();
    expect(contextFit(undefined, undefined)).toBeNull();
  });

  it('renders a scenario route with unknown window as unknown fit', () => {
    const unknown = loadFixtureDetail('unknown', 'r-9d0c31');
    expect(unknown?.routes[0].fits_context).toBeNull();
  });

  it('an exact-size conversation fits the route window', () => {
    expect(contextFit(400_000, 400_000)).toBe(true);
    expect(contextFit(400_001, 400_000)).toBe(false);
  });
});
