// A stand in for bin/airlock_console.py, so the page is developable and
// testable with no router and no Python. It answers the same routes with the
// same shapes, reading console/fixtures/ directly.
//
// Dev and preview only. Nothing here is bundled.

import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Connect, Plugin, ViteDevServer, PreviewServer } from 'vite';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { contextFit } from '../src/lib/state';
import {
  MOCK_CSRF_TOKEN as CONTROL_CSRF_TOKEN,
  MockControlState,
  handleControl,
  readRequestBody,
  sendJson,
} from './control';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = resolve(HERE, '..', 'fixtures');

interface Fixture {
  overviewFile: string;
  /** Drop the stream every second or so, to exercise reconnect and backoff. */
  flaky?: boolean;
  /** Answer every session detail request with a safe failure. */
  failDetails?: boolean;
  /** Never emit a frame, so the loading skeleton stays rendered. */
  holdStream?: boolean;
  /** Answer slowly, to exercise the loading state. */
  slowMs?: number;
}

const ALIASES: Record<string, Fixture> = {
  default: { overviewFile: 'overview.json' },
  reconnecting: { overviewFile: 'overview.json', flaky: true },
  loading: { overviewFile: 'overview.json', slowMs: 1500, holdStream: true },
  'detail-error': { overviewFile: 'overview-one.json', failDetails: true },
  // Phase 2 scenarios. Each seeds the mock control state from a fixture.
  proposal: { overviewFile: 'overview.json' },
  applied: { overviewFile: 'overview.json' },
  conflict: { overviewFile: 'overview.json' },
  states: { overviewFile: 'overview.json' },
  chains: { overviewFile: 'overview.json' },
  pinned: { overviewFile: 'overview-pinned.json' },
  uncontrollable: { overviewFile: 'overview-uncontrollable.json' },
  metered: { overviewFile: 'overview.json' },
  bare: { overviewFile: 'overview-bare.json' },
};

function safeKey(name: string | null): string {
  // The fixture name is a query parameter, so it never reaches a path unfiltered.
  return (name ?? 'default').replace(/[^a-zA-Z0-9_-]/g, '');
}

function fixtureFor(name: string | null): Fixture {
  const key = safeKey(name);
  if (ALIASES[key]) return ALIASES[key];
  return { overviewFile: `overview-${key}.json` };
}

function readJson<T>(file: string): T | null {
  const path = resolve(FIXTURES, file);
  if (!existsSync(path)) return null;
  return JSON.parse(readFileSync(path, 'utf8')) as T;
}

type Any = Record<string, any>;

function loadOverview(name: string | null): Any | null {
  return readJson<Any>(fixtureFor(name).overviewFile);
}

/**
 * A detail for a session that has no hand written fixture: the summary plus
 * the machine wide routes, narrowed to this session's context. Enough to
 * develop against without writing a file per session.
 */
function synthesizeDetail(overview: Any, id: string): Any | null {
  const summary = (overview.sessions ?? []).find((s: Any) => s.id === id);
  if (!summary) return null;
  const tokens = summary.context?.input_tokens ?? null;
  const routes = (overview.routes ?? []).map((r: Any) => ({
    ...r,
    fits_context: contextFit(tokens, r.context_window),
  }));
  const cooldowns = routes
    .filter((r: Any) => r.status === 'cooling')
    .map((r: Any) => ({
      scope: 'model',
      model: r.model,
      provider: r.provider,
      remaining_seconds: r.cooldown_remaining_seconds,
    }));
  return {
    ...summary,
    routes,
    cooldowns,
    chains: {},
    usage: [],
    events: [],
    last_handoff: null,
  };
}

export function loadFixtureDetail(name: string | null, id: string): Any | null {
  const overview = loadOverview(name);
  if (!overview) return null;
  const summary = (overview.sessions ?? []).find((s: Any) => s.id === id);
  // A hand-written detail from another scenario must never resurrect a session
  // the selected overview does not contain.
  if (!summary) return null;

  const safeName = safeKey(name);
  const safeId = id.replace(/[^a-zA-Z0-9._-]/g, '');
  const scenario = safeName ? readJson<Any>(`session-${safeName}-${safeId}.json`) : null;
  const own = scenario ?? readJson<Any>(`session-${safeId}.json`);
  if (!own) return synthesizeDetail(overview, id);

  // Keep the rich timeline/chains from the detail fixture, but the selected
  // overview is authoritative for all SessionSummary fields in that scenario.
  const tokens = summary.context?.input_tokens ?? null;
  const routeSource = own.routes ?? overview.routes ?? [];
  const routes = routeSource.map((r: Any) => ({
    ...r,
    fits_context: contextFit(tokens, r.context_window),
  }));
  return { ...own, ...summary, routes };
}

function send(res: ServerResponse, status: number, body: unknown, type = 'application/json') {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  res.statusCode = status;
  res.setHeader('Content-Type', `${type}; charset=utf-8`);
  res.setHeader('Cache-Control', 'no-store');
  res.end(text);
}

/** Cooldowns tick down between frames so the countdowns visibly move. */
function advance(overview: Any, seconds: number): Any {
  const step = (n: number | null) => (typeof n === 'number' ? Math.max(0, n - seconds) : n);
  return {
    ...overview,
    generated_at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
    routes: (overview.routes ?? []).map((r: Any) => ({
      ...r,
      cooldown_remaining_seconds: step(r.cooldown_remaining_seconds),
    })),
  };
}

const controlStates = new Map<string, MockControlState>();

function controlState(name: string | null): MockControlState {
  const key = safeKey(name);
  let state = controlStates.get(key);
  if (!state) {
    const seed = readJson<Any[]>(`proposals-${safeKey(name)}.json`) ?? [];
    state = new MockControlState(seed);
    const chains = readJson<Any>(`chains-${safeKey(name)}.json`);
    if (chains) state.chains = chains;
    controlStates.set(key, state);
  }
  return state;
}

function middleware(): Connect.NextHandleFunction {
  return (req: IncomingMessage, res: ServerResponse, next: Connect.NextFunction) => {
    const url = new URL(req.url ?? '/', 'http://127.0.0.1');

    // Deterministic screenshots and browser tests need a known control state.
    // Resetting on the page request itself means it happens exactly once per
    // load, rather than on every poll.
    if (url.searchParams.get('reset') === '1') {
      controlStates.delete(safeKey(url.searchParams.get('fixture')));
    }

    if (!url.pathname.startsWith('/api/') && url.pathname !== '/healthz') return next();

    const name = url.searchParams.get('fixture');
    const fixture = fixtureFor(name);
    const overview = loadOverview(name);

    // Phase 2 endpoints, including every human mutation.
    const isControl =
      url.pathname === '/api/chains' ||
      url.pathname.startsWith('/api/proposals') ||
      url.pathname.startsWith('/api/human/') ||
      url.pathname.startsWith('/api/tools/');
    if (isControl) {
      const state = controlState(name);
      void readRequestBody(req).then((body) => {
        const answered = handleControl(
          state,
          {
            method: req.method ?? 'GET',
            path: url.pathname,
            headers: req.headers,
            body,
            origin: (req.headers.origin as string | undefined) ?? null,
          },
          (sessionId) => loadFixtureDetail(name, sessionId),
        );
        if (!answered) return next();
        sendJson(res, answered.status, answered.body);
      });
      return;
    }

    const answer = (fn: () => void) => {
      if (fixture.slowMs) setTimeout(fn, fixture.slowMs);
      else fn();
    };

    // Phase 2 fields ride on the phase 1 payloads, exactly as the server does.
    const state = controlState(name);
    const withControl = (payload: Any): Any => ({
      ...payload,
      proposals: state.summaries(),
      chain_digest: state.digest(),
    });
    const withSessionControl = (detail: Any): Any => {
      const own = state.proposals.filter(
        (p) => p.kind === 'session_handoff' && p.session_id === detail.id,
      );
      const active = own
        .filter((p) => ['pending', 'applying', 'failed', 'conflicted'].includes(p.status))
        .sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0))[0];
      return {
        ...detail,
        controllable: detail.controllable ?? detail.state !== 'ended',
        pinned_model: detail.pinned_model ?? null,
        proposals: own,
        current_handoff: active ?? null,
      };
    };

    if (url.pathname === '/healthz') {
      return send(res, 200, { ok: true, sessions: overview?.sessions?.length ?? 0 });
    }

    if (!overview) {
      return send(res, 404, { error: `No fixture named ${name ?? 'default'}` });
    }

    // Session history and usage, synthesized so the pages can be exercised
    // offline. Deterministic, so screenshots and audits are stable.
    if (url.pathname === '/api/usage' || url.pathname.startsWith('/api/history')) {
      const day = (n: number) => `2026-09-${String(n).padStart(2, '0')}`;
      const stamp = (n: number, h = 9) => `${day(n)}T${String(h).padStart(2, '0')}:00:00Z`;
      const sessions = [1, 2, 3, 4, 5, 6].map((i) => ({
        id: `hx-fixture-${i}`,
        session_id: `fixture-${i}-0000-4000-8000-000000000000`,
        project: i % 2 ? 'claudex' : 'voice-agent',
        workdir: i % 2 ? 'C:\\work\\claudex' : 'C:\\work\\voice-agent',
        title: i === 1 ? 'Ship the console history page' : `Fixture session ${i}`,
        branch: i % 2 ? 'wip/unreleased-batch' : 'main',
        models: i % 3 === 0 ? ['gpt-5.6-sol', 'claude-opus-5[1m]'] : ['claude-fable-5-1'],
        primary_model: i % 3 === 0 ? 'gpt-5.6-sol' : 'claude-fable-5-1',
        provider: i % 3 === 0 ? 'openai' : 'anthropic',
        started_at: stamp(i),
        last_activity_at: stamp(i, 17),
        prompts: 10 * i,
        replies: 90 * i,
        tool_calls: 40 * i,
        compactions: i % 2,
        peak_context: 120_000 * i,
        last_context: 80_000 * i,
        window: 1_000_000,
        output_tokens: 250_000 * i,
        subagents: i,
        entrypoint: 'cli',
        version: '2.1.263',
      }));
      const agents = [
        { id: 'agentfixture0001', agent_type: 'airlock-sol', description: 'Fix red tests', model: 'gpt-5.6-sol', background: true, status: 'async_launched', started_at: stamp(1, 10), finished_at: stamp(1, 11), prompts: 1, replies: 12, tool_calls: 9, peak_context: 40_000, output_tokens: 3_000, depth: 1 },
        { id: 'agentfixture0002', agent_type: 'Explore', description: 'Map the launcher', model: 'claude-fable-5-1', background: false, status: 'completed', started_at: stamp(1, 12), finished_at: stamp(1, 12), prompts: 1, replies: 4, tool_calls: 6, peak_context: 25_000, output_tokens: 900, depth: 1 },
      ];
      const activity = [
        { at: stamp(1, 10), kind: 'prompt', preview: 'Make the history page useful.' },
        { at: stamp(1, 10), kind: 'tool', tool: 'Bash', preview: 'Run the console suite' },
        { at: stamp(1, 11), kind: 'reply', preview: 'Done, all green.' },
      ];
      if (url.pathname === '/api/usage') {
        const group = url.searchParams.get('group') ?? 'day';
        // A request with an upper bound is the previous-range comparison;
        // answer it with smaller numbers so the deltas have something to say.
        const scale = url.searchParams.get('until') ? 0.6 : 1;
        const series = [1, 2, 3, 4, 5, 6].map((i) => ({
          period: group === 'month' ? '2026-09' : group === 'week' ? `2026-W3${5 + (i > 3 ? 1 : 0)}` : day(i),
          sessions: 1 + (i % 2),
          prompts: 10 * i,
          replies: 90 * i,
          tool_calls: 40 * i,
          output_tokens: 250_000 * i,
          compactions: i % 2,
          by_provider: i % 3 === 0 ? { openai: 60 * i, anthropic: 30 * i } : { anthropic: 90 * i },
        }));
        return send(res, 200, {
          generated_at: stamp(6, 18),
          group,
          since: null,
          until: null,
          filters: { project: url.searchParams.get('project') || null, model: url.searchParams.get('model') || null },
          totals: {
            sessions: Math.round(6 * scale),
            prompts: Math.round(210 * scale),
            replies: Math.round(1890 * scale),
            tool_calls: Math.round(840 * scale),
            output_tokens: Math.round(5_250_000 * scale),
            compactions: Math.round(3 * scale),
            subagents: Math.round(21 * scale),
            peak_context_max: 720_000,
          },
          series,
          agent_types: [{ name: 'airlock-luna', count: 12 }, { name: 'Explore', count: 6 }, { name: 'airlock-sol', count: 3 }],
          tools: [{ name: 'Bash', count: 400 }, { name: 'Edit', count: 220 }, { name: 'Read', count: 150 }],
          projects: [{ name: 'claudex', sessions: 3, prompts: 90, tool_calls: 360, output_tokens: 2_250_000 }, { name: 'voice-agent', sessions: 3, prompts: 120, tool_calls: 480, output_tokens: 3_000_000 }],
          models: [{ name: 'claude-fable-5-1', replies: 1500, provider: 'anthropic' }, { name: 'gpt-5.6-sol', replies: 390, provider: 'openai' }],
          peaks: sessions.slice(0, 3).map((s) => ({ id: s.id, project: s.project, title: s.title, peak_context: s.peak_context, window: s.window })),
          peak_buckets: { under_25: 2, '25_to_50': 2, '50_to_75': 1, over_75: 1, unknown: 0 },
        });
      }
      if (url.pathname === '/api/history') {
        const project = url.searchParams.get('project');
        const listed = project ? sessions.filter((s) => s.project === project) : sessions;
        return send(res, 200, {
          generated_at: stamp(6, 18),
          refreshed_at: stamp(6, 18),
          total: sessions.length,
          projects: ['claudex', 'voice-agent'],
          models: ['claude-fable-5-1', 'claude-opus-5[1m]', 'gpt-5.6-sol'],
          sessions: listed,
        });
      }
      const feedMatch = /^\/api\/history\/([^/]+)\/subagents\/([^/]+)$/.exec(url.pathname);
      if (feedMatch) {
        return send(res, 200, { id: feedMatch[2], model: 'gpt-5.6-sol', last_activity_at: stamp(1, 11), context: { input_tokens: 40_000, window: 272_000 }, activity });
      }
      const agentsMatch = /^\/api\/history\/([^/]+)\/subagents$/.exec(url.pathname);
      if (agentsMatch) return send(res, 200, agents);
      const detailMatch = /^\/api\/history\/([^/]+)$/.exec(url.pathname);
      if (detailMatch) {
        const found = sessions.find((s) => s.id === detailMatch[1]);
        if (!found) return send(res, 404, { error: 'not_found' });
        return send(res, 200, {
          ...found,
          agents,
          periods: [{ kind: 'airlock', from: stamp(1, 9), to: stamp(1, 14), profile: 'hybrid-openai-root', root_model: 'gpt-5.6-sol', open: false }],
          airlock_inferred: false,
          activity,
        });
      }
    }
    if (url.pathname === '/api/overview') {
      return answer(() => send(res, 200, withControl(overview)));
    }
    if (url.pathname === '/api/sessions') {
      return answer(() => send(res, 200, overview.sessions ?? []));
    }
    if (url.pathname === '/api/routes') {
      return answer(() => send(res, 200, overview.routes ?? []));
    }

    const detailMatch = /^\/api\/sessions\/([^/]+)(\/events|\/report\.md)?$/.exec(url.pathname);
    if (detailMatch) {
      const id = decodeURIComponent(detailMatch[1]);
      if (fixture.failDetails && !detailMatch[2]) {
        return answer(() => send(res, 503, { error: 'Fixture detail unavailable' }));
      }
      const detail = loadFixtureDetail(name, id);
      if (!detail) return send(res, 404, { error: 'No such session' });
      if (detailMatch[2] === '/events') {
        return answer(() => send(res, 200, detail.events ?? []));
      }
      if (detailMatch[2] === '/report.md') {
        return answer(() =>
          send(res, 200, `# ${detail.project}\n\nThe dev mock does not render reports.\n`, 'text/markdown'),
        );
      }
      return answer(() => send(res, 200, withSessionControl(detail)));
    }

    if (url.pathname === '/api/stream') {
      res.statusCode = 200;
      res.setHeader('Content-Type', 'text/event-stream');
      res.setHeader('Cache-Control', 'no-store');
      res.setHeader('Connection', 'keep-alive');
      if (fixture.holdStream) {
        // Flush the 200 response without sending data. The initial GET is the
        // only thing that will eventually populate the page.
        res.write(': loading fixture\n\n');
        req.on('close', () => undefined);
        return;
      }
      let elapsed = 0;
      const frame = () => {
        elapsed += 3;
        res.write(`event: overview\ndata: ${JSON.stringify(withControl(advance(overview, elapsed)))}\n\n`);
      };
      res.write(`event: overview\ndata: ${JSON.stringify(withControl(overview))}\n\n`);
      const ticker = setInterval(frame, 3000);
      const beat = setInterval(() => res.write('event: heartbeat\ndata: {}\n\n'), 15000);
      const stop = () => {
        clearInterval(ticker);
        clearInterval(beat);
      };
      if (fixture.flaky) {
        setTimeout(() => {
          stop();
          res.destroy();
        }, 1200);
      }
      req.on('close', stop);
      return;
    }

    return send(res, 404, { error: 'Unknown route' });
  };
}

/**
 * The real console injects the CSRF meta into the root HTML only. The mock does
 * the same, with a fixed development token, so the page exercises the real
 * header path instead of a special dev branch.
 */
export const MOCK_CSRF_TOKEN = CONTROL_CSRF_TOKEN;

function injectCsrfMeta(html: string): string {
  if (html.includes('name="airlock-csrf"')) return html;
  return html.replace(
    /<\/head>/i,
    `<meta name="airlock-csrf" content="${MOCK_CSRF_TOKEN}"></head>`,
  );
}

export function mockApi(): Plugin {
  return {
    name: 'airlock-console-mock-api',
    // Dev and preview only. A production build must never carry the mock's
    // token: bin/airlock_console.py injects a per-process one at serve time,
    // and a baked-in literal would be the same guessable value everywhere.
    apply: (_config, env) => env.command === 'serve',
    transformIndexHtml: {
      order: 'post',
      handler: (html: string) => injectCsrfMeta(html),
    },
    configureServer(server: ViteDevServer) {
      server.middlewares.use(middleware());
    },
    configurePreviewServer(server: PreviewServer) {
      server.middlewares.use(middleware());
      // Preview serves the built file, so the meta is injected on the way out
      // exactly as bin/airlock_console.py does.
      server.middlewares.use((req, res, next) => {
        const url = new URL(req.url ?? '/', 'http://127.0.0.1');
        if (url.pathname !== '/' && url.pathname !== '/index.html') return next();
        const file = resolve(HERE, '..', 'dist', 'index.html');
        if (!existsSync(file)) return next();
        const html = injectCsrfMeta(readFileSync(file, 'utf8'));
        res.statusCode = 200;
        res.setHeader('Content-Type', 'text/html; charset=utf-8');
        res.setHeader('Cache-Control', 'no-store');
        res.end(html);
      });
    },
  };
}
