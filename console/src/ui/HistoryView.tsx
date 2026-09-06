// Every session this machine has run, past or present, with the filters a
// person reaches for: which project, which model, how recent, and words from
// the title. One session opens into its totals, when it ran through Airlock,
// its subagents with their own transcripts, and its recent activity.

import { useEffect, useMemo, useState } from 'preact/hooks';
import { Activity } from './Activity';
import { ModelBadge, ProviderDot } from './Glyph';
import { SessionStats } from './SessionStats';
import { getHistory, getHistoryDetail, getSubagentFeed, getSubagents } from '../lib/api';
import { inferProvider, modelDisplayName, shortNameLookup } from '../lib/names';
import { clockTime, compactTokens, relativeTime } from '../lib/time';
import type { HistoryDetail, HistoryItem, HistoryListing, SubagentFeed, SubagentSummary } from '../types';

export interface HistoryViewProps {
  now: number;
  /** History ids of the sessions the live page currently shows. */
  liveHistoryIds: Set<string>;
  /** Jump to the live page for a session that is still running. */
  onOpenLive(historyId: string): void;
}

type Range = '24h' | '7d' | '30d' | 'all';

const RANGE_HOURS: Record<Range, number | null> = { '24h': 24, '7d': 24 * 7, '30d': 24 * 30, all: null };

function sinceFor(range: Range, now: number): string | undefined {
  const hours = RANGE_HOURS[range];
  if (hours == null) return undefined;
  return new Date(now - hours * 3600 * 1000).toISOString();
}

function Row({
  item,
  selected,
  live,
  now,
  onSelect,
}: {
  item: HistoryItem;
  selected: boolean;
  live: boolean;
  now: number;
  onSelect(id: string): void;
}) {
  const short = shortNameLookup(undefined);
  const pct = item.peak_context != null && item.window ? Math.round((item.peak_context / item.window) * 100) : null;
  return (
    <tr class={`hrow${selected ? ' hrow-selected' : ''}`} aria-selected={selected} onClick={() => onSelect(item.id)}>
      <td class="hproj">
        <span class="hproj-name">{item.project || 'unknown'}</span>
        {live ? <span class="tag tag-using">live</span> : null}
      </td>
      <td class="htitle">
        <span class="htitle-text">{item.title || `Session ${item.session_id.slice(0, 8)}`}</span>
        {item.branch ? <span class="topic-branch mono">{item.branch}</span> : null}
      </td>
      <td class="hmodel" title={item.models.map((m) => modelDisplayName(m)).join(', ')}>
        <ModelBadge name={short(item.primary_model ?? 'unknown')} provider={item.provider} />
        {item.models.length > 1 ? <span class="faint tnum">{` +${item.models.length - 1}`}</span> : null}
      </td>
      <td class="tnum hwhen" title={item.last_activity_at ?? undefined}>
        {relativeTime(item.last_activity_at, now)}
      </td>
      <td class="tnum">{item.prompts}</td>
      <td class="tnum">{item.tool_calls}</td>
      <td class="tnum">{item.compactions}</td>
      <td class="tnum hpeak">
        {item.peak_context == null ? '' : compactTokens(item.peak_context)}
        {pct != null ? <span class="faint">{` ${pct}%`}</span> : null}
      </td>
      <td class="tnum">{item.subagents}</td>
    </tr>
  );
}

function SubagentRow({
  historyId,
  agent,
  now,
}: {
  historyId: string;
  agent: SubagentSummary;
  now: number;
}) {
  const [open, setOpen] = useState(false);
  const [feed, setFeed] = useState<SubagentFeed | null>(null);
  const [error, setError] = useState<string | null>(null);
  const short = shortNameLookup(undefined);
  useEffect(() => {
    if (!open || feed) return;
    const controller = new AbortController();
    getSubagentFeed(historyId, agent.id, controller.signal)
      .then(setFeed)
      .catch((e: Error) => setError(e.message));
    return () => controller.abort();
  }, [open, feed, historyId, agent.id]);
  const model = agent.model ?? feed?.model ?? null;
  return (
    <li class={`subagent${open ? ' subagent-open' : ''}`}>
      <button type="button" class="subagent-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span class="subagent-type">
          <ProviderDot provider={inferProvider(model, 'anthropic')} />
          {agent.agent_type ?? 'agent'}
        </span>
        <span class="subagent-desc">{agent.description ?? agent.id}</span>
        <span class="subagent-meta faint tnum">
          {model ? short(model) : ''}
          {agent.replies != null ? ` · ${agent.replies} replies` : ''}
          {agent.tool_calls != null ? ` · ${agent.tool_calls} tools` : ''}
          {agent.peak_context != null ? ` · ${compactTokens(agent.peak_context)} ctx` : ''}
          {agent.started_at ? ` · ${relativeTime(agent.started_at, now)}` : ''}
          {agent.background ? ' · background' : ''}
        </span>
      </button>
      {open ? (
        error ? (
          <p class="section-unknown">{`This subagent's transcript could not be read: ${error}`}</p>
        ) : feed ? (
          <Activity items={feed.activity} modelShort={short(model ?? 'agent')} />
        ) : (
          <p class="section-unknown">Reading the subagent transcript.</p>
        )
      ) : null}
    </li>
  );
}

function Detail({
  id,
  now,
  live,
  onOpenLive,
}: {
  id: string;
  now: number;
  live: boolean;
  onOpenLive(historyId: string): void;
}) {
  const [detail, setDetail] = useState<HistoryDetail | null>(null);
  const [agents, setAgents] = useState<SubagentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setAgents(null);
    setError(null);
    getHistoryDetail(id, controller.signal).then(setDetail).catch((e: Error) => setError(e.message));
    getSubagents(id, controller.signal).then(setAgents).catch(() => setAgents([]));
    return () => controller.abort();
  }, [id]);
  if (error) return <p class="section-unknown">{`This session could not be read: ${error}`}</p>;
  if (!detail) return <p class="section-unknown">Reading this session.</p>;
  const short = shortNameLookup(undefined);
  return (
    <div class="hdetail">
      <div class="head">
        <h2 class="hdetail-title">{detail.title || `Session ${detail.session_id.slice(0, 8)}`}</h2>
        <div class="path mono">{detail.workdir ?? detail.project}</div>
        <div class="topic">
          <ModelBadge name={modelDisplayName(detail.primary_model)} provider={detail.provider} strong />
          {detail.models.length > 1 ? (
            <span class="faint">{`also ${detail.models.slice(1).map((m) => modelDisplayName(m)).join(', ')}`}</span>
          ) : null}
          {detail.branch ? <span class="topic-branch mono">{detail.branch}</span> : null}
          <span class="faint tnum">
            {detail.started_at ? `${clockTime(detail.started_at, false)} to ` : ''}
            {detail.last_activity_at ? clockTime(detail.last_activity_at, false) : ''}
          </span>
          {detail.entrypoint === 'sdk-cli' ? <span class="tag">print mode</span> : null}
          {live ? (
            <button type="button" class="btn btn-ghost" onClick={() => onOpenLive(detail.id)}>
              Open live session
            </button>
          ) : null}
        </div>
      </div>
      <SessionStats facts={detail} now={now} />
      <section class="subagents" aria-label="Subagents">
        <h2 class="micro">
          Subagents <span class="faint">{agents ? agents.length : detail.subagents}</span>
        </h2>
        {agents == null ? (
          <p class="section-unknown">Listing subagents.</p>
        ) : agents.length === 0 ? (
          <p class="section-unknown">This session spawned no subagents.</p>
        ) : (
          <ul class="subagent-list">
            {agents.map((agent) => (
              <SubagentRow key={agent.id} historyId={id} agent={agent} now={now} />
            ))}
          </ul>
        )}
      </section>
      <Activity items={detail.activity} modelShort={short(detail.primary_model ?? 'claude')} primary />
    </div>
  );
}

export function HistoryView(props: HistoryViewProps) {
  const [listing, setListing] = useState<HistoryListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [project, setProject] = useState('');
  const [model, setModel] = useState('');
  const [range, setRange] = useState<Range>('7d');
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getHistory(
      { project, model, since: sinceFor(range, props.now), q, limit: 500 },
      controller.signal,
    )
      .then((l) => {
        setListing(l);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
    return () => controller.abort();
    // props.now changes every second; the listing refreshes on its own timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, model, range, q, tick]);

  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(timer);
  }, []);

  const sessions = listing?.sessions ?? [];
  const totals = useMemo(
    () => ({
      prompts: sessions.reduce((n, s) => n + (s.prompts ?? 0), 0),
      tools: sessions.reduce((n, s) => n + (s.tool_calls ?? 0), 0),
      compactions: sessions.reduce((n, s) => n + (s.compactions ?? 0), 0),
      subagents: sessions.reduce((n, s) => n + (s.subagents ?? 0), 0),
    }),
    [sessions],
  );

  return (
    <main class="history" id="session-view" aria-label="Session history">
      <h1 class="sr-only">Session history</h1>
      <section class="hfilters" aria-label="History filters">
        <div class="seg" role="radiogroup" aria-label="Active within">
          {(
            [
              ['24h', 'Today'],
              ['7d', '7 days'],
              ['30d', '30 days'],
              ['all', 'All time'],
            ] as const
          ).map(([key, label]) => (
            <button key={key} type="button" role="radio" class="chip" aria-checked={range === key} onClick={() => setRange(key)}>
              {label}
            </button>
          ))}
        </div>
        <label class="hselect">
          <span class="sr-only">Project</span>
          <select value={project} onChange={(e) => setProject((e.target as HTMLSelectElement).value)} aria-label="Project">
            <option value="">All projects</option>
            {(listing?.projects ?? []).map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </label>
        <label class="hselect">
          <span class="sr-only">Model</span>
          <select value={model} onChange={(e) => setModel((e.target as HTMLSelectElement).value)} aria-label="Model">
            <option value="">All models</option>
            {(listing?.models ?? []).map((m) => (
              <option key={m} value={m}>{modelDisplayName(m)}</option>
            ))}
          </select>
        </label>
        <label class="hsearch">
          <span class="sr-only">Search</span>
          <input
            type="search"
            placeholder="Search title, branch, or directory"
            value={q}
            onInput={(e) => setQ((e.target as HTMLInputElement).value)}
          />
        </label>
        {project || model || q || range !== '7d' ? (
          <button
            type="button"
            class="disc"
            onClick={() => {
              setProject('');
              setModel('');
              setQ('');
              setRange('7d');
            }}
          >
            Reset
          </button>
        ) : null}
        <span class="hsummary faint tnum">
          {listing
            ? `${sessions.length} of ${listing.total} sessions · ${totals.prompts} prompts · ${totals.tools} tool calls · ${totals.compactions} compactions · ${totals.subagents} subagents`
            : 'Reading the index.'}
          {listing?.refreshed_at ? ` · indexed ${relativeTime(listing.refreshed_at, props.now)}` : ''}
        </span>
      </section>

      <div class="hbody">
        <div class="hlist">
          {error ? (
            <p class="section-unknown">{`The history could not be read: ${error}`}</p>
          ) : listing && sessions.length === 0 ? (
            <p class="section-unknown">No session matches these filters.</p>
          ) : (
            <table class="htable">
              <colgroup>
                <col class="c-proj" />
                <col class="c-title" />
                <col class="c-model" />
                <col class="c-when" />
                <col class="c-n" />
                <col class="c-n" />
                <col class="c-n" />
                <col class="c-peak" />
                <col class="c-n" />
              </colgroup>
              <thead>
                <tr>
                  <th>Project</th>
                  <th>Title</th>
                  <th>Model</th>
                  <th>Active</th>
                  <th class="tnum">Prompts</th>
                  <th class="tnum">Tools</th>
                  <th class="tnum">Compact</th>
                  <th class="tnum">Peak ctx</th>
                  <th class="tnum">Agents</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((item) => (
                  <Row
                    key={item.id}
                    item={item}
                    selected={item.id === selected}
                    live={props.liveHistoryIds.has(item.id)}
                    now={props.now}
                    onSelect={setSelected}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
        <aside class="hpane" aria-label="Session history detail">
          {selected ? (
            <Detail
              id={selected}
              now={props.now}
              live={props.liveHistoryIds.has(selected)}
              onOpenLive={props.onOpenLive}
            />
          ) : (
            <div class="empty">
              <h2>Pick a session</h2>
              <p>
                Totals, peak context, compactions, when it ran through Airlock, its subagents
                with their own transcripts, and what it was doing last.
              </p>
              <p class="hint">
                Everything here is read from the transcripts on this machine. Previews are one
                line each, and nothing leaves the machine.
              </p>
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}
