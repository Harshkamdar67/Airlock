// Session grouping and ordering, and the rail's keyboard contract.

import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  filterSessions,
  groupSessions,
  groupSessionsByProject,
  railKeyAction,
  railOrder,
  sortSessions,
  stepSelection,
} from '../src/lib/group';
import type { Overview, SessionState, SessionSummary } from '../src/types';

const FIXTURES = resolve(__dirname, '..', 'fixtures');
const load = <T,>(name: string): T => JSON.parse(readFileSync(resolve(FIXTURES, name), 'utf8')) as T;

function make(
  id: string,
  state: SessionState,
  project: string,
  lastActivity: string | null,
): SessionSummary {
  return {
    id,
    state,
    blocked_reason: null,
    profile: 'p',
    root_model: 'gpt-5.6-sol',
    root_provider: 'openai',
    active_model: 'gpt-5.6-sol',
    workdir: `D:\\work\\${project}`,
    project,
    started_at: '2026-09-05T06:00:00Z',
    last_activity_at: lastActivity,
    context: { input_tokens: 1000, window: 400000 },
    workers: [],
    recent_handoffs: 0,
    url: 'http://127.0.0.1:1',
  };
}

const SESSIONS: SessionSummary[] = [
  make('a', 'idle', 'alpha', '2026-09-05T08:00:00Z'),
  make('b', 'running', 'bravo', '2026-09-05T09:00:00Z'),
  make('c', 'blocked', 'charlie', '2026-09-05T07:00:00Z'),
  make('d', 'running', 'delta', '2026-09-05T09:01:00Z'),
  make('e', 'ended', 'echo', '2026-09-05T05:00:00Z'),
  make('f', 'blocked', 'foxtrot', '2026-09-05T08:30:00Z'),
];

describe('grouping and sorting', () => {
  it('puts blocked first, then running, idle, ended', () => {
    expect(groupSessions(SESSIONS).map((g) => g.state)).toEqual([
      'blocked',
      'running',
      'idle',
      'ended',
    ]);
  });

  it('titles the blocked group so the reason to look is obvious', () => {
    expect(groupSessions(SESSIONS)[0].title).toBe('Needs attention');
  });

  it('sorts the most recently active to the top inside a group', () => {
    const groups = groupSessions(SESSIONS);
    expect(groups[0].sessions.map((s) => s.id)).toEqual(['f', 'c']);
    expect(groups[1].sessions.map((s) => s.id)).toEqual(['d', 'b']);
  });

  it('omits a group with nothing in it', () => {
    const only = groupSessions([make('x', 'running', 'x', null)]);
    expect(only).toHaveLength(1);
    expect(only[0].state).toBe('running');
  });

  it('falls back to the start time when there is no activity yet', () => {
    const list = [make('n1', 'idle', 'n1', null), make('n2', 'idle', 'n2', '2026-09-05T08:00:00Z')];
    expect(sortSessions(list).map((s) => s.id)).toEqual(['n2', 'n1']);
  });

  it('never drops a session whose state it does not recognise', () => {
    const odd = { ...make('z', 'running', 'zulu', null), state: 'hibernating' as SessionState };
    const groups = groupSessions([...SESSIONS, odd]);
    expect(railOrder(groups).map((s) => s.id)).toContain('z');
    expect(groups.at(-1)?.title).toBe('Other');
  });

  it('groups the twelve session fixture without losing one', () => {
    const twelve = load<Overview>('overview-twelve.json');
    expect(railOrder(groupSessions(twelve.sessions))).toHaveLength(12);
  });
});

describe('filtering', () => {
  it('matches on project, id, model and path', () => {
    expect(filterSessions(SESSIONS, 'char').map((s) => s.id)).toEqual(['c']);
    expect(filterSessions(SESSIONS, 'sol')).toHaveLength(6);
    expect(filterSessions(SESSIONS, 'D:\\work\\alpha').map((s) => s.id)).toEqual(['a']);
  });

  it('ignores case and surrounding space', () => {
    expect(filterSessions(SESSIONS, '  BRAVO ').map((s) => s.id)).toEqual(['b']);
  });

  it('keeps everything for an empty query', () => {
    expect(filterSessions(SESSIONS, '')).toHaveLength(6);
  });
});

describe('rail keyboard navigation', () => {
  const ordered = railOrder(groupSessions(SESSIONS));
  // f, c, d, b, a, e
  const ids = ordered.map((s) => s.id);

  it('walks the visual order, not the input order', () => {
    expect(ids).toEqual(['f', 'c', 'd', 'b', 'a', 'e']);
  });

  it('moves down and up across group boundaries', () => {
    expect(railKeyAction('ArrowDown', ordered, 'c')).toEqual({ type: 'select', id: 'd' });
    expect(railKeyAction('ArrowUp', ordered, 'd')).toEqual({ type: 'select', id: 'c' });
  });

  it('accepts j and k from anywhere on the page', () => {
    expect(railKeyAction('j', ordered, 'f')).toEqual({ type: 'select', id: 'c' });
    expect(railKeyAction('k', ordered, 'c')).toEqual({ type: 'select', id: 'f' });
  });

  it('stops at the ends instead of wrapping', () => {
    expect(railKeyAction('ArrowUp', ordered, 'f')).toEqual({ type: 'select', id: 'f' });
    expect(railKeyAction('ArrowDown', ordered, 'e')).toEqual({ type: 'select', id: 'e' });
  });

  it('jumps to the first and last with Home and End', () => {
    expect(railKeyAction('Home', ordered, 'b')).toEqual({ type: 'select', id: 'f' });
    expect(railKeyAction('End', ordered, 'b')).toEqual({ type: 'select', id: 'e' });
  });

  it('opens with Enter and with Space, which is what moves focus', () => {
    expect(railKeyAction('Enter', ordered, 'd')).toEqual({ type: 'open', id: 'd' });
    expect(railKeyAction(' ', ordered, 'd')).toEqual({ type: 'open', id: 'd' });
  });

  it('starts at the top when nothing is selected yet', () => {
    expect(railKeyAction('ArrowDown', ordered, null)).toEqual({ type: 'select', id: 'f' });
    expect(railKeyAction('ArrowUp', ordered, null)).toEqual({ type: 'select', id: 'e' });
  });

  it('ignores keys it does not own, so typing still reaches the page', () => {
    expect(railKeyAction('t', ordered, 'd')).toBeNull();
    expect(railKeyAction('Tab', ordered, 'd')).toBeNull();
    expect(railKeyAction('a', ordered, 'd')).toBeNull();
  });

  it('never opens a selection that the current filter hid', () => {
    const visible = [make('relay', 'running', 'airlock-relay', '2026-09-05T09:00:00Z')];
    expect(railKeyAction('Enter', visible, 'claudex')).toEqual({
      type: 'open',
      id: 'relay',
    });
    expect(railKeyAction(' ', visible, 'claudex')).toEqual({
      type: 'open',
      id: 'relay',
    });
  });

  it('does nothing on an empty rail', () => {
    expect(railKeyAction('ArrowDown', [], null)).toBeNull();
    expect(stepSelection([], null, 1)).toBeNull();
  });
});

describe('project grouping', () => {
  const byProject = (list: SessionSummary[]) =>
    groupSessionsByProject(list).map((g) => [g.title, g.sessions.map((s) => s.id)]);

  it('puts every session of a directory under one heading, blocked projects first', () => {
    const twoInAlpha = [
      ...SESSIONS,
      { ...make('a2', 'running', 'alpha', '2026-09-05T10:00:00Z') },
    ];
    const groups = groupSessionsByProject(twoInAlpha);
    expect(groups.every((g) => g.kind === 'project')).toBe(true);
    // Blocked projects lead regardless of how recent the others are.
    expect(groups.slice(0, 2).map((g) => g.title).sort()).toEqual(['charlie', 'foxtrot']);
    expect(groups[0].title).toBe('foxtrot');
    const alpha = groups.find((g) => g.title === 'alpha');
    expect(alpha?.sessions.map((s) => s.id)).toEqual(['a2', 'a']);
    expect(alpha?.workdir).toBe('D:\\work\\alpha');
  });

  it('orders sessions inside a project by attention, then recency', () => {
    const list = [
      make('x1', 'idle', 'x', '2026-09-05T09:00:00Z'),
      make('x2', 'blocked', 'x', '2026-09-05T06:00:00Z'),
      make('x3', 'running', 'x', '2026-09-05T07:00:00Z'),
      make('x4', 'running', 'x', '2026-09-05T08:00:00Z'),
    ];
    expect(byProject(list)).toEqual([['x', ['x2', 'x4', 'x3', 'x1']]]);
  });

  it('treats the same directory with different case or a trailing slash as one project', () => {
    const list = [
      { ...make('p', 'idle', 'proj', null), workdir: 'D:\\Work\\Proj\\' },
      { ...make('q', 'idle', 'proj', null), workdir: 'd:\\work\\proj' },
    ];
    expect(groupSessionsByProject(list)).toHaveLength(1);
  });

  it('keeps a session with no directory as its own group named by id', () => {
    const list = [{ ...make('r-9f3a2c1b', 'idle', '', null), workdir: '' }];
    expect(groupSessionsByProject(list)[0].title).toBe('Session 9f3a2c');
  });

  it('walks the rail in the rendered order', () => {
    const groups = groupSessionsByProject(SESSIONS);
    expect(railOrder(groups).map((s) => s.id)).toEqual(groups.flatMap((g) => g.sessions.map((s) => s.id)));
  });
});
