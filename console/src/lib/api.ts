// The console's only network surface. Same origin, no credentials, no headers
// beyond Accept. The fixture name rides along as a query parameter so the dev
// mock can answer without the page knowing it is a mock.

import type {
  HistoryDetail,
  HistoryListing,
  Overview,
  SessionDetail,
  SubagentFeed,
  SubagentSummary,
  UsageReport,
} from '../types';

export function fixtureName(): string | null {
  if (typeof location === 'undefined') return null;
  const p = new URLSearchParams(location.search);
  return p.get('fixture');
}

/**
 * Dev and preview only: the mock selects a scenario from this parameter. The
 * real console ignores it, and the CSRF token never appears here.
 */
export function withFixture(path: string): string {
  const name = fixtureName();
  if (!name) return path;
  return `${path}${path.includes('?') ? '&' : '?'}fixture=${encodeURIComponent(name)}`;
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(withFixture(path), {
    headers: { Accept: 'application/json' },
    credentials: 'omit',
    signal,
  });
  if (!res.ok) throw new Error(`${path} answered ${res.status}`);
  return (await res.json()) as T;
}

export function getOverview(signal?: AbortSignal): Promise<Overview> {
  return getJson<Overview>('/api/overview', signal);
}

export function getSession(id: string, signal?: AbortSignal): Promise<SessionDetail> {
  return getJson<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`, signal);
}

// ---------------------------------------------------------------------------
// Session history
// ---------------------------------------------------------------------------

export interface HistoryQuery {
  project?: string;
  model?: string;
  since?: string;
  until?: string;
  q?: string;
  limit?: number;
}

export function getHistory(query: HistoryQuery = {}, signal?: AbortSignal): Promise<HistoryListing> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      params.set(key, String(value));
    }
  }
  const suffix = params.toString();
  return getJson<HistoryListing>(`/api/history${suffix ? `?${suffix}` : ''}`, signal);
}

export interface UsageQuery {
  group?: 'day' | 'week' | 'month';
  project?: string;
  model?: string;
  since?: string;
  until?: string;
}

export function getUsage(query: UsageQuery = {}, signal?: AbortSignal): Promise<UsageReport> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      params.set(key, String(value));
    }
  }
  const suffix = params.toString();
  return getJson<UsageReport>(`/api/usage${suffix ? `?${suffix}` : ''}`, signal);
}

export function getHistoryDetail(id: string, signal?: AbortSignal): Promise<HistoryDetail> {
  return getJson<HistoryDetail>(`/api/history/${encodeURIComponent(id)}`, signal);
}

export function getSubagents(id: string, signal?: AbortSignal): Promise<SubagentSummary[]> {
  return getJson<SubagentSummary[]>(`/api/history/${encodeURIComponent(id)}/subagents`, signal);
}

export function getSubagentFeed(
  id: string,
  agentId: string,
  signal?: AbortSignal,
): Promise<SubagentFeed> {
  return getJson<SubagentFeed>(
    `/api/history/${encodeURIComponent(id)}/subagents/${encodeURIComponent(agentId)}`,
    signal,
  );
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

export type StreamState = 'connecting' | 'open' | 'reconnecting';

export interface StreamHandle {
  close(): void;
}

export interface StreamOptions {
  onOverview(o: Overview): void;
  onState(state: StreamState, retryInSeconds?: number): void;
  /** Injectable for tests. */
  factory?: (url: string) => EventSource;
}

const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000];

/**
 * Supervised SSE. EventSource retries on its own with a schedule the page
 * cannot see, so the connection is closed on error and reopened here instead,
 * which is what lets the topbar say when the next attempt happens.
 */
export function openOverviewStream(opts: StreamOptions): StreamHandle {
  const make = opts.factory ?? ((url: string) => new EventSource(url));
  let source: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stableTimer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let stopped = false;

  const connect = () => {
    if (stopped) return;
    opts.onState(attempt === 0 ? 'connecting' : 'reconnecting');
    const es = make(withFixture('/api/stream'));
    source = es;

    es.addEventListener('open', () => {
      opts.onState('open');
      // A TCP connection that opens and falls over immediately is still a
      // failure. Reset the backoff only after it has stayed healthy for 30s.
      if (stableTimer) clearTimeout(stableTimer);
      stableTimer = setTimeout(() => {
        attempt = 0;
        stableTimer = null;
      }, 30_000);
    });

    es.addEventListener('overview', (ev) => {
      try {
        opts.onOverview(JSON.parse((ev as MessageEvent).data) as Overview);
      } catch {
        // A malformed frame is dropped. The next full overview replaces it.
      }
    });

    es.addEventListener('error', () => {
      if (stopped) return;
      es.close();
      source = null;
      if (stableTimer) clearTimeout(stableTimer);
      stableTimer = null;
      const wait = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
      attempt += 1;
      opts.onState('reconnecting', Math.round(wait / 1000));
      timer = setTimeout(connect, wait);
    });
  };

  connect();

  return {
    close() {
      stopped = true;
      if (timer) clearTimeout(timer);
      if (stableTimer) clearTimeout(stableTimer);
      source?.close();
      source = null;
    },
  };
}
