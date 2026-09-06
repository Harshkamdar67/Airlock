// The session view: who and where, then what unblocks it, then the timeline.

import type { ComponentChildren } from 'preact';
import { Bar, Glyph, ModelBadge, Status, StatusMark } from './Glyph';
import { Activity } from './Activity';
import { SessionStats } from './SessionStats';
import { Timeline } from './Timeline';
import { compactTokens, uptime } from '../lib/time';
import {
  hasWorkdir,
  inferProvider,
  isUnrouted,
  profileLabel,
  providerName,
  sessionSubtitle,
  sessionTitle,
  shortNameLookup,
} from '../lib/names';
import { resolveBlocked } from '../lib/resolution';
import { sessionState } from '../lib/vocab';
import type { SentenceContext, TimelineFilters } from '../lib/sentences';
import type { SessionDetail } from '../types';

export interface SessionViewProps {
  detail: SessionDetail;
  now: number;
  filters: TimelineFilters;
  onFilters(f: TimelineFilters): void;
  onCopyReport(): void;
  copied: string | null;
  headingRef: { current: HTMLHeadingElement | null };
  /** Phase 2 proposal panel, rendered between the verdict and the timeline. */
  proposals?: ComponentChildren;
}

export function SessionView(props: SessionViewProps) {
  const d = props.detail;
  const short = shortNameLookup(d.routes);
  const ctx: SentenceContext = {
    short,
    windowOf: (model) => d.routes?.find((r) => r.model === model)?.context_window ?? null,
  };
  const state = sessionState(d.state, d.blocked_reason);
  const resolution = resolveBlocked(d);
  const activeRoute = d.routes?.find((r) => r.model === d.active_model);
  const tokens = d.context?.input_tokens ?? null;
  const window = d.context?.window ?? null;
  const up = uptime(d.started_at, props.now);
  const pinned = d.active_model !== d.root_model;
  const native = isUnrouted(d);
  const direct = d.source === 'airlock-direct';
  // A field the router never reported is omitted and named once below, rather
  // than printed as a column of "unknown". Context has its own honest cell.
  const missing = [up === 'unknown' && !native ? 'uptime' : null].filter(
    (x): x is string => x !== null,
  );

  return (
    <main class="main" id="session-view" aria-label={`Session ${sessionTitle(d)}`}>
      <div class="head">
        <h1 tabIndex={-1} ref={props.headingRef as never}>
          {sessionTitle(d)}
        </h1>
        <div class={`path${hasWorkdir(d) ? ' mono' : ''}`}>{sessionSubtitle(d)}</div>
        {d.title || d.branch ? (
          <div class="topic">
            {d.title ? <span class="topic-title">{d.title}</span> : null}
            {d.branch ? <span class="topic-branch mono">{d.branch}</span> : null}
          </div>
        ) : null}

        <dl class="facts">
          <div class={`fact fact-${d.state}`}>
            <dt class="micro">State</dt>
            <dd>
              <Status d={state} />
            </dd>
          </div>
          <div class="fact">
            <dt class="micro">{pinned ? 'Pinned to' : 'Active model'}</dt>
            <dd>
              <ModelBadge
                name={short(d.active_model)}
                provider={activeRoute?.provider ?? inferProvider(d.active_model, d.root_provider)}
                strong
              />
              <span class="muted">{providerName(activeRoute?.provider ?? d.root_provider)}</span>
              {activeRoute?.metered ? <span class="tag tag-metered">metered</span> : null}
            </dd>
            {pinned ? (
              <dd class="fact-sub">
                <Glyph id="arrow" />
                {`root is ${short(d.root_model)}`}
              </dd>
            ) : null}
          </div>
          <div class="fact">
            <dt class="micro">Context</dt>
            {tokens == null ? (
              <dd class="muted">{native ? 'not tracked' : 'no request yet'}</dd>
            ) : (
              <dd class="tnum">
                {compactTokens(tokens)}
                {window ? ` of ${compactTokens(window)}` : ''}
                {window ? (
                  <span class="faint">{` ${Math.round((tokens / window) * 100)}%`}</span>
                ) : null}
                <Bar used={tokens} total={window} label="Context used" />
              </dd>
            )}
          </div>
          {up === 'unknown' ? null : (
            <div class="fact">
              <dt class="micro">Uptime</dt>
              <dd class="tnum">{up}</dd>
            </div>
          )}
          <div class="fact">
            <dt class="micro">Profile</dt>
            <dd class="muted">{profileLabel(d.profile)}</dd>
          </div>
        </dl>
        {missing.length ? (
          <p class="section-unknown">
            {`${missing.join(' and ').replace(/^./, (c) => c.toUpperCase())} ${
              missing.length === 1 ? 'is' : 'are'
            } unknown until ${
              d.controllable === true
                ? 'this session sends its first request.'
                : "this session's router is updated."
            }`}
          </p>
        ) : null}
      </div>

      {d.history ? <SessionStats facts={d.history} now={props.now} /> : null}

      {native ? (
        <section class="native-note" aria-label="About this session">
          <h2>{direct ? 'No router on this profile' : 'Plain Claude Code'}</h2>
          {direct ? (
            <p>
              This session runs on an Airlock profile that talks to the local proxy directly,
              so there is no session router. There are no routes, handoffs, or pins here, and
              its model is read from the session transcript's last reply.
            </p>
          ) : (
            <p>
              This session was started with <code>claude</code>, not <code>airlock</code>, so
              Airlock is not routing it. There are no routes, handoffs, or pins here, and its
              model is read from the session transcript's last reply.
            </p>
          )}
          <p class="faint">
            {direct
              ? 'Start it with a hybrid profile, for example '
              : 'Start it with '}
            <code>{direct ? 'airlock hybrid sol' : 'airlock'}</code>
            {' in the same directory to get failover, handoffs, and control from this page.'}
          </p>
        </section>
      ) : null}

      {resolution && !native ? (
        <section class="resolution" aria-label="What unblocks this session">
          <h2>What unblocks this</h2>
          <p>{resolution.lead}</p>
          <ul>
            {resolution.options.map((o) => (
              <li key={o.text}>
                <StatusMark
                  d={
                    o.tone === 'ok'
                      ? { label: '', glyph: 'run', tone: 'ok' }
                      : o.tone === 'unknown'
                        ? { label: '', glyph: 'unknown', tone: 'faint' }
                        : { label: '', glyph: 'unav', tone: 'faint' }
                  }
                />
                <span>
                  {o.text}
                  {o.detail ? <span class="faint">{` ${o.detail}`}</span> : null}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {props.proposals}

      {d.activity !== undefined ? (
        <Activity items={d.activity} modelShort={short(d.active_model)} primary={native} />
      ) : null}

      {native ? null : (
        <Timeline
          events={d.events ?? []}
          routes={d.routes ?? []}
          ctx={ctx}
          filters={props.filters}
          onFilters={props.onFilters}
          onCopyReport={props.onCopyReport}
          copied={props.copied}
        />
      )}
    </main>
  );
}
