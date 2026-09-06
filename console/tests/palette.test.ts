import { describe, expect, it } from 'vitest';
import { matchCommands, scoreMatch, type Command } from '../src/lib/palette';

const COMMANDS: Command[] = [
  { id: 'go-claudex', label: 'Go to claudex', group: 'Session', keywords: ['r-8f2c1a', 'claude-fable-5-1[1m]'] },
  { id: 'go-relay', label: 'Go to airlock-relay', group: 'Session', keywords: ['r-2b77e0'] },
  { id: 'go-quant', label: 'Go to agentquant', group: 'Session', keywords: ['r-c91d44'] },
  { id: 'filter-handoffs', label: 'Filter timeline to handoffs', group: 'Timeline' },
  { id: 'filter-cooldowns', label: 'Filter timeline to cooldowns', group: 'Timeline' },
  { id: 'copy-report', label: 'Copy report as Markdown', group: 'Session', keywords: ['export', 'markdown'] },
  { id: 'theme', label: 'Switch theme', group: 'View', keywords: ['dark', 'light'] },
  { id: 'shortcuts', label: 'Show keyboard shortcuts', group: 'Help', keywords: ['keys'] },
];

const labels = (q: string) => matchCommands(COMMANDS, q).map((m) => m.command.label);

describe('scoring', () => {
  it('ranks a prefix of the label highest', () => {
    const prefix = scoreMatch('Copy report as Markdown', 'copy')!;
    const inside = scoreMatch('Switch theme', 'theme')!;
    expect(prefix.score).toBeGreaterThan(inside.score);
  });

  it('ranks a word start above a mid word hit', () => {
    const wordStart = scoreMatch('Switch theme', 'the')!;
    const midWord = scoreMatch('Switch theme', 'eme')!;
    expect(wordStart.score).toBeGreaterThan(midWord.score);
  });

  it('falls back to characters in order', () => {
    const sub = scoreMatch('Filter timeline to handoffs', 'fth')!;
    expect(sub.score).toBeGreaterThan(0);
    expect(sub.score).toBeLessThan(600);
  });

  it('reports the characters it matched, for highlighting', () => {
    expect(scoreMatch('Switch theme', 'theme')!.ranges).toEqual([7, 8, 9, 10, 11]);
  });

  it('returns null when the characters are not all there', () => {
    expect(scoreMatch('Switch theme', 'zzz')).toBeNull();
    expect(scoreMatch('Switch theme', 'themex')).toBeNull();
  });

  it('treats an empty query as a match on everything', () => {
    expect(scoreMatch('anything', '')).toEqual({ score: 0, ranges: [] });
  });
});

describe('command matching', () => {
  it('keeps the given order for an empty query', () => {
    expect(labels('')).toEqual(COMMANDS.map((c) => c.label));
  });

  it('finds a session by project name after two characters', () => {
    expect(labels('cl')[0]).toBe('Go to claudex');
  });

  it('finds a session by its router id', () => {
    expect(labels('2b77')[0]).toBe('Go to airlock-relay');
  });

  it('finds a session by the model it is on', () => {
    expect(labels('fable')[0]).toBe('Go to claudex');
  });

  it('is case insensitive', () => {
    expect(labels('CLAUDEX')[0]).toBe('Go to claudex');
  });

  it('prefers a label hit over a keyword hit of the same shape', () => {
    // "dark" is only a keyword of Switch theme, so it still wins its own query.
    expect(labels('dark')[0]).toBe('Switch theme');
    // "Copy" starts a label, so it outranks anything matching by keyword.
    expect(labels('copy')[0]).toBe('Copy report as Markdown');
  });

  it('narrows as more is typed', () => {
    expect(labels('filter').length).toBe(2);
    expect(labels('filter c')).toEqual(['Filter timeline to cooldowns']);
  });

  it('drops everything when nothing matches', () => {
    expect(labels('qqqq')).toEqual([]);
  });

  it('breaks ties on the shorter label, so the more specific command wins', () => {
    const ties = matchCommands(
      [
        { id: 'a', label: 'Theme settings and preferences', group: 'View' },
        { id: 'b', label: 'Theme', group: 'View' },
      ],
      'theme',
    );
    expect(ties[0].command.label).toBe('Theme');
  });

  it('does not highlight anything when the match came from a keyword', () => {
    const hit = matchCommands(COMMANDS, 'markdown').find((m) => m.command.id === 'copy-report')!;
    expect(hit.ranges).toEqual([]);
  });
});
