import { describe, expect, it } from 'vitest';
import {
  clockTime,
  compactTokens,
  countdown,
  minuteKey,
  relativeTime,
  shortDuration,
  spokenDuration,
  spokenMillis,
  uptime,
} from '../src/lib/time';

const NOW = Date.parse('2026-09-05T09:02:12Z');
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();

describe('relative time', () => {
  it('says just now for the last few seconds', () => {
    expect(relativeTime(ago(0), NOW)).toBe('just now');
    expect(relativeTime(ago(4), NOW)).toBe('just now');
  });

  it('counts seconds, then minutes, then hours, then days', () => {
    expect(relativeTime(ago(7), NOW)).toBe('7s ago');
    expect(relativeTime(ago(181), NOW)).toBe('3m ago');
    expect(relativeTime(ago(1860), NOW)).toBe('31m ago');
    expect(relativeTime(ago(8460), NOW)).toBe('2h 21m ago');
    expect(relativeTime(ago(7200), NOW)).toBe('2h ago');
    expect(relativeTime(ago(3 * 86400 + 4 * 3600), NOW)).toBe('3d 4h ago');
  });

  it('reads a future instant forwards', () => {
    expect(relativeTime(ago(-2880), NOW)).toBe('in 48m');
  });

  it('says unknown rather than inventing a time', () => {
    expect(relativeTime(null, NOW)).toBe('unknown');
    expect(relativeTime(undefined, NOW)).toBe('unknown');
    expect(relativeTime('not a date', NOW)).toBe('unknown');
  });
});

describe('durations', () => {
  it('is compact in a column', () => {
    expect(shortDuration(45)).toBe('45s');
    expect(shortDuration(2890)).toBe('48m');
    expect(shortDuration(8460)).toBe('2h 21m');
  });

  it('is spoken in a sentence', () => {
    expect(spokenDuration(2920)).toBe('49 minutes');
    expect(spokenDuration(60)).toBe('1 minute');
    expect(spokenDuration(1)).toBe('1 second');
    expect(spokenDuration(4800)).toBe('1 hour 20 minutes');
    expect(spokenDuration(7200)).toBe('2 hours');
  });

  it('admits when it does not know', () => {
    expect(spokenDuration(null)).toBe('an unknown time');
    expect(spokenDuration(undefined)).toBe('an unknown time');
  });

  it('formats a request duration', () => {
    expect(spokenMillis(14020)).toBe('14.0 seconds');
    expect(spokenMillis(640)).toBe('0.6 seconds');
    expect(spokenMillis(undefined)).toBeNull();
  });

  it('counts a cooldown down without ever going negative', () => {
    expect(countdown(2890)).toBe('48m left');
    expect(countdown(30)).toBe('under a minute left');
    expect(countdown(0)).toBe('any moment');
    expect(countdown(null)).toBe('unknown');
  });

  it('reports uptime from a start time', () => {
    expect(uptime('2026-09-05T06:41:03Z', NOW)).toBe('2h 21m');
    expect(uptime('nonsense', NOW)).toBe('unknown');
  });
});

describe('clock and minute keys', () => {
  it('renders a wall clock, with and without seconds', () => {
    expect(clockTime('2026-09-05T08:58:40Z')).toBe('08:58:40');
    expect(clockTime('2026-09-05T08:58:40Z', false)).toBe('08:58');
  });

  it('buckets by minute', () => {
    expect(minuteKey('2026-09-05T08:58:40Z')).toBe('2026-09-05 08:58');
    expect(minuteKey('2026-09-05T08:58:00Z')).toBe('2026-09-05 08:58');
    expect(minuteKey('2026-09-05T08:57:59Z')).toBe('2026-09-05 08:57');
  });
});

describe('token counts', () => {
  it('reads the way a person quotes them', () => {
    expect(compactTokens(612340)).toBe('612k');
    expect(compactTokens(1000000)).toBe('1M');
    expect(compactTokens(2000000)).toBe('2M');
    expect(compactTokens(262144)).toBe('262k');
    expect(compactTokens(8100)).toBe('8.1k');
    expect(compactTokens(940)).toBe('940');
  });

  it('says unknown instead of zero', () => {
    expect(compactTokens(null)).toBe('unknown');
    expect(compactTokens(undefined)).toBe('unknown');
    expect(compactTokens(0)).toBe('0');
  });
});
