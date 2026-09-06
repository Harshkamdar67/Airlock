// The sessions rail. One listbox, one tab stop, arrow keys walk every session
// across group boundaries. Selection follows the arrow keys because selecting
// only changes what is displayed. Sessions are grouped by the project they run
// in, the way people think about their work; the project name is the group
// header, so each row identifies itself by model, state and age.

import { useEffect, useRef } from 'preact/hooks';
import { Bar, Glyph, ModelBadge, Status } from './Glyph';
import { relativeTime } from '../lib/time';
import { inferProvider, profileLabel, sessionTitle, shortNameLookup } from '../lib/names';
import { sessionState } from '../lib/vocab';
import { railKeyAction, railOrder, type SessionGroup } from '../lib/group';
import type { SessionSummary } from '../types';

export interface RailProps {
  groups: SessionGroup[];
  selectedId: string | null;
  query: string;
  now: number;
  onQuery(q: string): void;
  onSelect(id: string): void;
  onOpen(id: string): void;
  filterRef: { current: HTMLInputElement | null };
  listRef: { current: HTMLDivElement | null };
  totalSessions: number;
  loading?: boolean;
}

function shortId(id: string): string {
  return id.replace(/^r-/, '').slice(0, 6);
}

function Row({
  session,
  selected,
  now,
  onSelect,
  onOpen,
}: {
  session: SessionSummary;
  selected: boolean;
  now: number;
  onSelect(id: string): void;
  onOpen(id: string): void;
}) {
  const short = shortNameLookup(undefined);
  const state = sessionState(session.state, session.blocked_reason);
  const ctx = session.context;
  const pinned = session.active_model !== session.root_model;
  const provider = inferProvider(session.active_model, session.root_provider);
  const pct =
    ctx?.input_tokens != null && ctx.window
      ? Math.max(0, Math.min(100, Math.round((ctx.input_tokens / ctx.window) * 100)))
      : null;
  return (
    <div
      id={`session-option-${session.id}`}
      role="option"
      aria-selected={selected}
      aria-label={`${sessionTitle(session)}, ${state.label}, on ${short(session.active_model)}`}
      class={`srow srow-${session.state}${session.state === 'ended' ? ' srow-ended' : ''}`}
      onClick={() => onSelect(session.id)}
      onDblClick={() => onOpen(session.id)}
    >
      <span class="name sr-only">{sessionTitle(session)}</span>
      <span class="l1">
        <ModelBadge name={short(session.active_model)} provider={provider} strong />
        <Status d={state} className="srow-state" />
        <span class="when tnum">{relativeTime(session.last_activity_at ?? session.started_at, now)}</span>
      </span>
      {session.title ? <span class="ttl">{session.title}</span> : null}
      <span class="l2">
        {pinned ? (
          <span class="pinned">
            <Glyph id="arrow" />
            {`pinned, root ${short(session.root_model)}`}
          </span>
        ) : (
          <span class="faint">{profileLabel(session.profile)}</span>
        )}
        <span class="sid mono">{shortId(session.id)}</span>
      </span>
      {pct == null ? null : (
        <span class="l3">
          <Bar
            used={ctx?.input_tokens ?? null}
            total={ctx?.window ?? null}
            label={`${sessionTitle(session)} context`}
          />
          <span class="pct tnum">{`${pct}%`}</span>
        </span>
      )}
    </div>
  );
}

export function Rail(props: RailProps) {
  const { groups, selectedId, now, onSelect, onOpen } = props;
  const ordered = railOrder(groups);
  const listEl = props.listRef;
  const scrollRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!selectedId) return;
    const el = document.getElementById(`session-option-${selectedId}`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [selectedId]);

  useEffect(() => {
    if (!ordered.length || ordered.some((session) => session.id === selectedId)) return;
    onSelect(ordered[0].id);
  }, [ordered, selectedId, onSelect]);

  const onKeyDown = (e: KeyboardEvent) => {
    const action = railKeyAction(e.key, ordered, selectedId);
    if (!action) return;
    e.preventDefault();
    if (action.type === 'open') onOpen(action.id);
    else onSelect(action.id);
  };

  return (
    <nav class="rail" aria-label="Sessions" ref={scrollRef as never}>
      <div class="filter">
        <label class="sr-only" for="rail-filter">
          Filter sessions
        </label>
        <div class="filter-box">
          <Glyph id="search" />
          <input
            id="rail-filter"
            type="search"
            autocomplete="off"
            placeholder="Filter by project or model"
            value={props.query}
            ref={props.filterRef as never}
            onInput={(e) => props.onQuery((e.target as HTMLInputElement).value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault();
                if (props.query) props.onQuery('');
                else listEl.current?.focus();
              }
              if (e.key === 'Enter' || e.key === 'ArrowDown') {
                e.preventDefault();
                listEl.current?.focus();
              }
            }}
          />
          <span class="kbd" aria-hidden="true">/</span>
        </div>
      </div>

      {ordered.length === 0 ? (
        <p class="railempty">
          {props.loading
            ? 'Looking for Airlock sessions on this machine.'
            : props.totalSessions === 0
              ? 'No Airlock sessions are running on this machine.'
              : `No session matches “${props.query}”.`}
        </p>
      ) : (
        <div
          class="railbox"
          role="listbox"
          aria-label="Sessions by project, arrow keys to move"
          tabIndex={0}
          ref={listEl as never}
          aria-activedescendant={selectedId ? `session-option-${selectedId}` : undefined}
          onKeyDown={onKeyDown}
        >
          {groups.map((g) => {
            const needs = g.sessions.some((s) => s.state === 'blocked');
            return (
              <div
                role="group"
                aria-label={`${g.title}, ${g.sessions.length}`}
                class={`railproj${needs ? ' railproj-attn' : ''}`}
                key={g.workdir ?? g.title}
              >
                <h2 class="railgroup" aria-hidden="true" title={g.workdir ?? undefined}>
                  <span class="railgroup-name">{g.title}</span>
                  <span class="railgroup-n tnum">{g.sessions.length}</span>
                </h2>
                <div class="slist">
                  {g.sessions.map((s) => (
                    <Row
                      key={s.id}
                      session={s}
                      selected={s.id === selectedId}
                      now={now}
                      onSelect={onSelect}
                      onOpen={onOpen}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </nav>
  );
}
