// The inspector: what has room, in what order, and what the router would try
// next. Ready first, then cooling with a countdown, then unavailable.

import type { ComponentChildren } from 'preact';
import { Bar, Glyph, ModelBadge, Status } from './Glyph';
import { clockTime, compactTokens, relativeTime } from '../lib/time';
import { isUnrouted, providerName, sessionTitle, shortNameLookup, titled } from '../lib/names';
import {
  categoryLabel,
  contextFitLabel,
  routeState,
  sharedUnknowns,
  sharedUnknownNotice,
  UNKNOWN,
  type UnknownFields,
} from '../lib/vocab';
import type { ProviderHeadroom, RouteStatus, SessionDetail } from '../types';

const STATUS_ORDER = { ready: 0, cooling: 1, unavailable: 2 } as const;

function rank(r: RouteStatus): number {
  const base = STATUS_ORDER[r.status] ?? 3;
  // Inside "ready", something that fits this conversation comes first.
  const fit =
    r.status !== 'ready' ? 0 : r.fits_context === true ? 0 : r.fits_context == null ? 0.25 : 0.5;
  return base + fit;
}

function Route({
  r,
  sessionId,
  onUse,
  quiet,
}: {
  r: RouteStatus;
  sessionId: string;
  onUse?: (route: RouteStatus) => void;
  /** Fields the whole section already reported as unknown, said once above. */
  quiet: UnknownFields;
}) {
  const using = r.sessions_using?.includes(sessionId);
  // Proposing a cooling or oversized route would only earn a server refusal.
  const offerable = r.status === 'ready' && r.fits_context !== false && !using;
  // An unknown tier is noise on every row; the section says it once.
  const showTier = !quiet.tier && (r.category !== 'unknown' || r.metered);
  return (
    <li class={`route${using ? ' route-using' : ''}${r.status !== 'ready' ? ' route-off' : ''}`}>
      <div class="r1">
        <ModelBadge name={r.short_name} provider={r.provider} strong />
        {using ? <span class="tag tag-using">in use here</span> : null}
        <Status d={routeState(r.status, r.cooldown_remaining_seconds)} />
      </div>
      <div class="r2">
        <span>{providerName(r.provider)}</span>
        {showTier ? (
          <span class={`tag${r.metered ? ' tag-metered' : ''}`}>{categoryLabel(r.category)}</span>
        ) : null}
        {quiet.window ? null : (
          <span class="tnum">
            {r.context_window ? `${compactTokens(r.context_window)} window` : 'window unknown'}
          </span>
        )}
        {quiet.fit ? null : r.fits_context == null ? (
          <span class="st st-faint unknown">
            <Glyph id="unknown" />
            {contextFitLabel(r.fits_context)}
          </span>
        ) : r.fits_context ? (
          <span class="st st-ok">
            <Glyph id="run" />
            {contextFitLabel(r.fits_context)}
          </span>
        ) : (
          <span class="nofit">{contextFitLabel(r.fits_context)}</span>
        )}
        {onUse && offerable ? (
          <button
            type="button"
            class="btn btn-ghost route-use"
            onClick={() => onUse(r)}
            aria-label={`Use ${r.short_name} for this session`}
          >
            {`Use ${r.short_name}`}
          </button>
        ) : null}
      </div>
    </li>
  );
}

function RouteSection({
  d,
  onUse,
}: {
  d: SessionDetail;
  onUse?: (route: RouteStatus) => void;
}) {
  if (isUnrouted(d)) {
    return (
      <section class="sec" id="inspector-routes">
        <h2 class="micro">Routes for this session</h2>
        <p class="section-unknown">
          {d.source === 'airlock-direct'
            ? 'None. This profile talks to the proxy directly, so there is no router and nothing to route between. A hybrid profile shows its routes here.'
            : 'None. Airlock is not routing this session, so it uses whatever Claude Code chose on its own. Start it with airlock to see its routes here.'}
        </p>
      </section>
    );
  }
  const routes = [...(d.routes ?? [])].sort((a, b) => rank(a) - rank(b));
  // Said once for the whole list rather than repeated on every row.
  const quiet = sharedUnknowns(routes);
  const notice = sharedUnknownNotice(quiet, d.controllable === true);
  const buckets: [string, RouteStatus[]][] = [
    ['Ready', routes.filter((r) => r.status === 'ready')],
    ['Cooling', routes.filter((r) => r.status === 'cooling')],
    ['Unavailable', routes.filter((r) => r.status === 'unavailable')],
  ];
  return (
    <section class="sec" id="inspector-routes">
      <h2 class="micro">Routes for this session</h2>
      {notice ? <p class="section-unknown">{notice}</p> : null}
      {routes.length === 0 ? (
        <p class="faint">This router has not reported its routes.</p>
      ) : (
        buckets.map(([title, list]) =>
          list.length ? (
            <div key={title}>
              <h3 class="micro subhead">
                {title} <span class="faint">{list.length}</span>
              </h3>
              <ul class="routes">
                {list.map((r) => (
                  <Route key={r.model} r={r} sessionId={d.id} onUse={onUse} quiet={quiet} />
                ))}
              </ul>
            </div>
          ) : null,
        )
      )}
    </section>
  );
}

function ChainSection({ d }: { d: SessionDetail }) {
  const short = shortNameLookup(d.routes);
  const chain = d.chains?.[d.active_model];
  if (!chain?.length) return null;
  const models = [d.active_model, ...chain];
  return (
    <section class="sec">
      <h2 class="micro">Chain for {short(d.active_model)}</h2>
      <ul class="chain">
        {models.map((model, i) => {
          const r = d.routes?.find((x) => x.model === model);
          const state = r ? routeState(r.status, r.cooldown_remaining_seconds) : UNKNOWN;
          const tooBig = r?.fits_context === false;
          const cls =
            i === 0
              ? `on ${tooBig || r?.status === 'unavailable' ? 'unav' : r?.status === 'cooling' ? 'cooling' : 'ok'}`
              : r?.status === 'ready' && !tooBig
                ? 'ok'
                : '';
          return (
            <li class={cls} key={model}>
              <span class="spine">
                <span class="dot" />
              </span>
              <span>
                <span class={i === 0 ? 'm' : ''}>{short(model)}</span>
                {i === 0 ? <span class="faint"> active</span> : null}
              </span>
              {tooBig ? (
                <Status d={{ label: 'too large', glyph: 'unav', tone: 'faint' }} />
              ) : r?.status === 'ready' && r.fits_context == null ? (
                <Status d={{ label: 'ready, fit unknown', glyph: 'unknown', tone: 'faint' }} />
              ) : (
                <Status d={state} />
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function HeadroomSection({ headroom, now }: { headroom: ProviderHeadroom[]; now: number }) {
  return (
    <section class="sec">
      <h2 class="micro">Plan headroom</h2>
      <ul class="headroom">
        {headroom.map((h) => (
          <li key={h.provider}>
            <div class="h1r">
              <span>{h.provider}</span>
              <span class="faint">{h.window}</span>
              {h.used_percent == null ? (
                <span class="pct">
                  <Status d={UNKNOWN} className="unknown" />
                </span>
              ) : (
                <span class="pct tnum">{`${h.used_percent}% used`}</span>
              )}
            </div>
            {h.used_percent == null ? (
              <>
                <div class="un" />
                <div class="faint when">Airlock has no source for this plan yet.</div>
              </>
            ) : (
              <>
                <Bar used={h.used_percent} total={100} label={`${h.provider} plan used`} />
                {h.resets_at ? (
                  <div class="faint when">
                    {`Resets ${clockTime(h.resets_at, false)}, ${relativeTime(h.resets_at, now)}`}
                  </div>
                ) : null}
              </>
            )}
          </li>
        ))}
        {headroom.length === 0 ? <li class="faint">No provider has reported a plan window.</li> : null}
      </ul>
    </section>
  );
}

function WorkerSection({ d }: { d: SessionDetail }) {
  const short = shortNameLookup(d.routes);
  if (!d.workers?.length) return null;
  return (
    <section class="sec">
      <h2 class="micro">Workers</h2>
      <ul class="workers">
        {d.workers.map((w) => {
          const r = d.routes?.find((x) => x.model === w.model);
          return (
            <li key={w.model}>
              <span class="m">{titled(short(w.model))}</span>
              {r ? <span class="faint">{r.provider}</span> : null}
              <span class="n tnum">
                {w.requests} {w.requests === 1 ? 'request' : 'requests'}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function Inspector({
  detail,
  headroom,
  now,
  onUseRoute,
  chainEditor,
}: {
  detail: SessionDetail;
  headroom: ProviderHeadroom[];
  now: number;
  onUseRoute?: (route: RouteStatus) => void;
  chainEditor?: ComponentChildren;
}) {
  return (
    <aside
      class="inspector"
      id="inspector-panel"
      tabIndex={0}
      aria-label={`Routes and limits for ${sessionTitle(detail)}`}
    >
      <div class="igrid">
        <RouteSection d={detail} onUse={onUseRoute} />
        <div>
          <ChainSection d={detail} />
          {chainEditor}
          <HeadroomSection headroom={headroom} now={now} />
          <WorkerSection d={detail} />
        </div>
      </div>
    </aside>
  );
}
