// Grouping and ordering for the sessions rail, plus the flat order the arrow
// keys walk. Blocked sessions always come first; inside a group the most
// recently active is on top.

import { parseTime } from './time';
import { deriveShortName } from './names';
import { SESSION_GROUP_ORDER, SESSION_GROUP_TITLES } from './vocab';
import type { SessionState, SessionSummary } from '../types';

export interface SessionGroup {
  state: SessionState;
  title: string;
  sessions: SessionSummary[];
  /** Project groups carry the directory the sessions run in. */
  kind?: 'state' | 'project';
  workdir?: string;
}

function activityRank(s: SessionSummary): number {
  const t = parseTime(s.last_activity_at) ?? parseTime(s.started_at);
  return t ?? 0;
}

const STATE_RANK: Record<string, number> = { blocked: 0, running: 1, idle: 2, ended: 3 };

function stateRank(s: SessionSummary): number {
  return STATE_RANK[s.state] ?? 4;
}

/** Blocked first, then running, idle, ended; most recently active on top inside a state. */
export function sortSessionsByAttention(sessions: SessionSummary[]): SessionSummary[] {
  return [...sessions].sort((a, b) => {
    const s = stateRank(a) - stateRank(b);
    if (s !== 0) return s;
    const d = activityRank(b) - activityRank(a);
    if (d !== 0) return d;
    return a.id.localeCompare(b.id);
  });
}

function groupKey(s: SessionSummary): string {
  return s.workdir?.trim() ? s.workdir.trim().replace(/[\\/]+$/, '').toLowerCase() : `id:${s.id}`;
}

/**
 * One group per project directory, the way people think about their work.
 * A project with a blocked session rises to the top, then projects follow
 * their most recent activity. Inside a project the same attention order
 * applies, so the session that needs a person is always first.
 */
export function groupSessionsByProject(sessions: SessionSummary[]): SessionGroup[] {
  const byKey = new Map<string, SessionSummary[]>();
  for (const s of sessions) {
    const key = groupKey(s);
    const list = byKey.get(key);
    if (list) list.push(s);
    else byKey.set(key, [s]);
  }
  const groups: SessionGroup[] = [];
  for (const list of byKey.values()) {
    const ordered = sortSessionsByAttention(list);
    const lead = ordered[0];
    groups.push({
      kind: 'project',
      state: lead.state,
      title: lead.project || `Session ${lead.id.replace(/^r-/, '').slice(0, 6)}`,
      workdir: lead.workdir,
      sessions: ordered,
    });
  }
  return groups.sort((a, b) => {
    const s = stateRank(a.sessions[0]) - stateRank(b.sessions[0]);
    if (s !== 0) return s;
    const d =
      Math.max(...b.sessions.map(activityRank)) - Math.max(...a.sessions.map(activityRank));
    if (d !== 0) return d;
    return a.title.localeCompare(b.title);
  });
}

export function sortSessions(sessions: SessionSummary[]): SessionSummary[] {
  return [...sessions].sort((a, b) => {
    const d = activityRank(b) - activityRank(a);
    if (d !== 0) return d;
    const p = a.project.localeCompare(b.project);
    if (p !== 0) return p;
    return a.id.localeCompare(b.id);
  });
}

/** Case insensitive substring over the fields a person would type. */
export function matchesQuery(s: SessionSummary, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = [
    s.project,
    s.id,
    s.profile,
    s.active_model,
    s.root_model,
    s.root_provider,
    s.workdir,
    deriveShortName(s.active_model),
    deriveShortName(s.root_model),
  ]
    .join(' ')
    .toLowerCase();
  return hay.includes(q);
}

export function filterSessions(sessions: SessionSummary[], query: string): SessionSummary[] {
  return sessions.filter((s) => matchesQuery(s, query));
}

/** Groups in rail order, empty groups omitted. */
export function groupSessions(sessions: SessionSummary[]): SessionGroup[] {
  const groups: SessionGroup[] = [];
  const seen = new Set<SessionSummary>();
  for (const state of SESSION_GROUP_ORDER) {
    const inState = sortSessions(sessions.filter((s) => s.state === state));
    inState.forEach((s) => seen.add(s));
    if (inState.length) {
      groups.push({ state, title: SESSION_GROUP_TITLES[state], sessions: inState });
    }
  }
  // A state the console does not recognise is still shown, never dropped.
  const rest = sortSessions(sessions.filter((s) => !seen.has(s)));
  if (rest.length) {
    groups.push({ state: rest[0].state, title: 'Other', sessions: rest });
  }
  return groups;
}

/** The order arrow keys walk, which is the order the rail renders. */
export function railOrder(groups: SessionGroup[]): SessionSummary[] {
  return groups.flatMap((g) => g.sessions);
}

export type RailAction =
  | { type: 'select'; id: string }
  | { type: 'open'; id: string }
  | null;

/**
 * The rail's whole keyboard contract, as a function of the pressed key and the
 * current list. Arrows and j/k select, which only changes what is displayed;
 * Enter and Space open, which moves focus into the session view.
 */
export function railKeyAction(
  pressed: string,
  ordered: SessionSummary[],
  currentId: string | null,
): RailAction {
  if (!ordered.length) return null;
  switch (pressed) {
    case 'ArrowDown':
    case 'j': {
      const id = stepSelection(ordered, currentId, 1);
      return id ? { type: 'select', id } : null;
    }
    case 'ArrowUp':
    case 'k': {
      const id = stepSelection(ordered, currentId, -1);
      return id ? { type: 'select', id } : null;
    }
    case 'Home':
      return { type: 'select', id: ordered[0].id };
    case 'End':
      return { type: 'select', id: ordered[ordered.length - 1].id };
    case 'Enter':
    case ' ': {
      const visible = ordered.some((session) => session.id === currentId)
        ? currentId
        : ordered[0].id;
      return visible ? { type: 'open', id: visible } : null;
    }
    default:
      return null;
  }
}

/** Next id for an arrow key press, wrapping at neither end. */
export function stepSelection(
  ordered: SessionSummary[],
  currentId: string | null,
  delta: number,
): string | null {
  if (!ordered.length) return null;
  const i = ordered.findIndex((s) => s.id === currentId);
  if (i < 0) return delta >= 0 ? ordered[0].id : ordered[ordered.length - 1].id;
  const next = Math.min(ordered.length - 1, Math.max(0, i + delta));
  return ordered[next].id;
}
