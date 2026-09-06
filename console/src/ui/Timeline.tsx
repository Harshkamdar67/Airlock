// The timeline: one sentence per event, newest first, grouped by minute.
// Plain completed requests collapse to a single quiet row per model.

import { useState } from 'preact/hooks';
import { Glyph } from './Glyph';
import { clockTime } from '../lib/time';
import { titled } from '../lib/names';
import {
  buildTimeline,
  eventModels,
  type MinuteGroup,
  type Piece,
  type Sentence,
  type SentenceContext,
  type TimelineFilters,
} from '../lib/sentences';
import { KIND_GROUPS, KIND_GROUP_TITLES, type KindGroup } from '../lib/vocab';
import type { AirEvent, RouteStatus } from '../types';

function Pieces({ pieces }: { pieces: Piece[] }) {
  return (
    <>
      {pieces.map((p, i) => {
        if (p.t === 'text') return <span key={i}>{p.v}</span>;
        if (p.t === 'raw') return <code class="raw" key={i}>{p.v}</code>;
        return (
          <span class="m" key={i}>
            {p.v}
          </span>
        );
      })}
    </>
  );
}

function Spoken({ s }: { s: Sentence }) {
  return (
    <div class={`ev${s.tone === 'attn' ? ' ev-attn' : ''}`}>
      <time class="tnum" dateTime={s.timestamp}>
        {clockTime(s.timestamp)}
      </time>
      <p>
        <Pieces pieces={s.pieces} />
        {s.note ? <span class="note">{s.note}</span> : null}
        {s.hop ? (
          <span class="hopnote">
            <span>{s.hop.from}</span>
            <Glyph id="arrow" />
            <span>{s.hop.to}</span>
          </span>
        ) : null}
      </p>
    </div>
  );
}

function Quiet({
  summary,
  sentences,
  timestamps,
  id,
}: {
  summary: string;
  sentences: Sentence[];
  timestamps: string[];
  id: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div class="ev ev-quiet">
      <time class="tnum" dateTime={timestamps[0]}>
        {clockTime(timestamps[0])}
      </time>
      <div>
        <p>
          {summary}.
          <button
            type="button"
            class="disc"
            aria-expanded={open}
            aria-controls={`quiet-${id}`}
            onClick={() => setOpen(!open)}
          >
            {open ? 'Hide' : 'Show'}
          </button>
        </p>
        {open ? (
          <ul class="quietlist" id={`quiet-${id}`}>
            {sentences.map((s, i) => (
              <li key={s.timestamp + i}>
                <time class="tnum" dateTime={s.timestamp}>
                  {clockTime(s.timestamp)}
                </time>
                <span>
                  <Pieces pieces={s.pieces} />
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}

export interface TimelineProps {
  events: AirEvent[];
  routes: RouteStatus[];
  ctx: SentenceContext;
  filters: TimelineFilters;
  onFilters(f: TimelineFilters): void;
  onCopyReport(): void;
  copied: string | null;
}

export function Timeline(props: TimelineProps) {
  const group = props.filters.group ?? 'all';
  const groups: MinuteGroup[] = buildTimeline(props.events, props.ctx, props.filters);
  const models = [...new Set(props.events.flatMap((e) => eventModels(e)))];

  return (
    <>
      <div class="tools">
        <div role="radiogroup" aria-label="Filter timeline by kind" style="display:contents">
          {KIND_GROUPS.map((g: KindGroup) => (
            <button
              key={g}
              type="button"
              class="chip"
              role="radio"
              tabIndex={group === g ? 0 : -1}
              aria-checked={group === g}
              onClick={() => props.onFilters({ ...props.filters, group: g })}
              onKeyDown={(e) => {
                if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
                e.preventDefault();
                const i = KIND_GROUPS.indexOf(g);
                const step = e.key === 'ArrowRight' ? 1 : -1;
                const next = KIND_GROUPS[(i + step + KIND_GROUPS.length) % KIND_GROUPS.length];
                props.onFilters({ ...props.filters, group: next });
                const buttons = (e.currentTarget.parentElement as HTMLElement).querySelectorAll<HTMLButtonElement>(
                  '[role="radio"]',
                );
                buttons[(i + step + KIND_GROUPS.length) % KIND_GROUPS.length]?.focus();
              }}
            >
              {KIND_GROUP_TITLES[g]}
            </button>
          ))}
        </div>
        <div class="spacer" />
        <label class="micro" for="model-filter">
          Model
        </label>
        <select
          id="model-filter"
          value={props.filters.model ?? ''}
          onChange={(e) =>
            props.onFilters({ ...props.filters, model: (e.target as HTMLSelectElement).value || null })
          }
        >
          <option value="">All models</option>
          {models.map((m) => {
            const route = props.routes.find((r) => r.model === m);
            return (
              <option value={m} key={m}>
                {titled(route?.short_name ?? props.ctx.short(m))}
              </option>
            );
          })}
        </select>
        <button type="button" class="chip" onClick={props.onCopyReport}>
          {props.copied ? props.copied : 'Copy report'}
        </button>
      </div>

      <section class="timeline" aria-label="Timeline, newest first">
        {groups.length === 0 ? (
          <p class="railempty" style="padding-left:0">
            {props.events.length
              ? 'No events match this filter.'
              : 'This session has not recorded an event yet.'}
          </p>
        ) : (
          <>
            {groups.map((g) => (
              <div key={g.key}>
                <div class="minute">
                  <span class="t tnum">{g.label}</span>
                  <hr class="rule" />
                </div>
                {g.rows.map((row) =>
                  row.type === 'quiet' ? (
                    <Quiet
                      key={row.id}
                      id={row.id.replace(/[^\w-]/g, '')}
                      summary={row.summary}
                      sentences={row.sentences}
                      timestamps={row.events.map((e) => e.timestamp)}
                    />
                  ) : (
                    <Spoken key={row.id} s={row.sentence} />
                  ),
                )}
              </div>
            ))}
            {props.events.length <= 1 ? (
              <p class="tl-quiet">
                Nothing else has happened yet. Requests, handoffs, cooldowns and overflow
                will appear here as this session works, newest first.
              </p>
            ) : null}
          </>
        )}
      </section>
    </>
  );
}
