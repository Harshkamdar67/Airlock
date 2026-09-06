// Every router event becomes one sentence a person would actually say.
//
// A sentence is returned as pieces rather than a string so the page can set
// model names in a heavier weight without parsing text back apart, and so the
// tests can assert on the flattened text.

import { compactTokens, minuteKey, spokenDuration, spokenMillis } from './time';
import { providerName, titled, type ShortNames } from './names';
import { kindGroups, type KindGroup } from './vocab';
import type { AirEvent } from '../types';

export type Piece =
  | { t: 'text'; v: string }
  | { t: 'model'; v: string }
  | { t: 'provider'; v: string }
  | { t: 'raw'; v: string };

export type SentenceTone = 'normal' | 'attn' | 'quiet';

export interface Sentence {
  timestamp: string;
  /** Raw kind, or the empty string for a plain request event. */
  kind: string;
  pieces: Piece[];
  note?: string;
  hop?: { from: string; to: string };
  tone: SentenceTone;
  groups: KindGroup[];
  /** Every model this row is about, for the model filter. */
  models: string[];
}

export interface SentenceContext {
  short: ShortNames;
  /** Context window of a model in tokens, or null when the console has no idea. */
  windowOf: (model: string | null | undefined) => number | null;
}

const t = (v: string): Piece => ({ t: 'text', v });
const m = (v: string): Piece => ({ t: 'model', v });
const p = (v: string): Piece => ({ t: 'provider', v });

/** Flatten to the plain sentence, which is what the tests and the report use. */
export function sentenceText(s: Sentence): string {
  return s.pieces.map((piece) => piece.v).join('');
}

/** Every model id an event refers to, deduplicated, for filtering. */
export function eventModels(ev: AirEvent): string[] {
  const out = [ev.model, ev.from_model, ev.to_model, ev.failover_from];
  return [...new Set(out.filter((x): x is string => !!x))];
}

function handoffPair(ev: AirEvent): { from?: string; to?: string } {
  return {
    from: ev.from_model ?? ev.failover_from,
    to: ev.to_model ?? ev.model,
  };
}

/** A plain request event, the kind with a status and an outcome and no kind. */
function requestSentence(ev: AirEvent, ctx: SentenceContext): Sentence {
  const model = ctx.short(ev.model);
  const base = {
    timestamp: ev.timestamp,
    kind: '',
    groups: kindGroups(undefined),
    models: eventModels(ev),
  };
  const took = spokenMillis(ev.duration_ms);

  if (ev.outcome === 'completed') {
    if (ev.failover_from) {
      const from = ctx.short(ev.failover_from);
      const why =
        ev.reason === 'rate_limit'
          ? ' after a rate limit'
          : ev.reason === 'overflow'
            ? ' after the conversation was too large'
            : '';
      return {
        ...base,
        tone: 'normal',
        pieces: [
          m(titled(model)),
          t(' picked up from '),
          m(titled(from)),
          t(`${why}${took ? ` and completed in ${took}.` : ' and completed.'}`),
        ],
      };
    }
    // Quiet. Rendered inside a collapsed group, never as its own sentence.
    return {
      ...base,
      tone: 'quiet',
      pieces: [m(titled(model)), t(took ? ` completed in ${took}.` : ' completed.')],
    };
  }

  if (ev.outcome === 'rate_limited' || ev.status === 429) {
    return {
      ...base,
      tone: 'normal',
      pieces: ev.failover_from
        ? [
            m(titled(model)),
            t(' was rate limited too, after taking over from '),
            m(titled(ctx.short(ev.failover_from))),
            t('.'),
          ]
        : [m(titled(model)), t(' was rate limited.')],
    };
  }

  if (ev.outcome === 'rate_limit_exhausted') {
    return {
      ...base,
      tone: 'attn',
      pieces: [m(titled(model)), t(' returned a rate limit and nothing was left to try.')],
    };
  }

  if (ev.outcome === 'overflow_exhausted') {
    return {
      ...base,
      tone: 'attn',
      pieces: [m(titled(model)), t(' had no route left with a large enough window.')],
    };
  }

  if (typeof ev.status === 'number' && ev.status >= 400) {
    return {
      ...base,
      tone: 'normal',
      pieces: [m(titled(model)), t(` failed with status ${ev.status}.`)],
    };
  }

  return {
    ...base,
    tone: 'normal',
    pieces: [m(titled(model)), t(` finished with outcome ${ev.outcome ?? 'unknown'}.`)],
  };
}

/**
 * Turn one event into one sentence. Never returns null: an event the console
 * has no wording for still renders, with its raw kind visible.
 */
export function renderEvent(ev: AirEvent, ctx: SentenceContext): Sentence {
  const kind = ev.kind;
  if (!kind) return requestSentence(ev, ctx);

  const base = {
    timestamp: ev.timestamp,
    kind,
    groups: kindGroups(kind),
    models: eventModels(ev),
    tone: 'normal' as SentenceTone,
  };
  const model = ctx.short(ev.model);
  const prov = providerName(ev.provider);

  switch (kind) {
    case 'rate_limit_failover_attempted': {
      const { from, to } = handoffPair(ev);
      const toName = ctx.short(to);
      const considered =
        typeof ev.models_considered === 'number'
          ? ` after considering ${ev.models_considered} ${ev.models_considered === 1 ? 'route' : 'routes'}`
          : '';
      if (!from) {
        return { ...base, pieces: [t('The router handed off to '), m(titled(toName)), t(`${considered}.`)] };
      }
      const fromName = ctx.short(from);
      return {
        ...base,
        hop: { from: fromName, to: toName },
        pieces: [
          m(titled(fromName)),
          t(' was rate limited. The router handed off to '),
          m(titled(toName)),
          t(`${considered}.`),
        ],
      };
    }

    case 'rate_limit_failover_succeeded': {
      const { from, to } = handoffPair(ev);
      if (!from || !to) {
        return { ...base, pieces: [t('The rate-limit handoff succeeded.')] };
      }
      const fromName = ctx.short(from);
      const toName = ctx.short(to);
      return {
        ...base,
        hop: { from: fromName, to: toName },
        pieces: [
          m(titled(toName)),
          t(' took over from '),
          m(titled(fromName)),
          t(' after a rate limit.'),
        ],
      };
    }

    case 'rate_limit_cooldown_skipped':
      return {
        ...base,
        pieces: [m(titled(model)), t(' was skipped without a request; it is already cooling.')],
      };

    case 'rate_limit_provider_cooldown':
      return {
        ...base,
        pieces:
          typeof ev.remaining_seconds === 'number'
            ? [p(prov), t(` is cooling for ${spokenDuration(ev.remaining_seconds)}.`)]
            : [p(prov), t(' is cooling, so the rest of its models were skipped.')],
      };

    case 'rate_limit_chain_exhausted': {
      const routes =
        typeof ev.models_considered === 'number'
          ? ` after ${ev.models_considered} ${ev.models_considered === 1 ? 'route' : 'routes'}`
          : '';
      return {
        ...base,
        tone: 'attn',
        note: 'Nothing was left to try. The session is waiting for a route to open.',
        pieces: [m(titled(model)), t(`’s chain is exhausted${routes}.`)],
      };
    }

    case 'failover_overflow_attempted': {
      const { from, to } = handoffPair(ev);
      const fromName = ctx.short(from);
      const toName = ctx.short(to);
      return {
        ...base,
        hop: from && to ? { from: fromName, to: toName } : undefined,
        pieces: [
          t('The conversation did not fit '),
          m(titled(fromName)),
          t(', so the router moved it to '),
          m(titled(toName)),
          t('.'),
        ],
      };
    }

    case 'failover_overflow_succeeded': {
      const { from, to } = handoffPair(ev);
      if (!from || !to) {
        return { ...base, pieces: [t('The context-overflow handoff succeeded.')] };
      }
      const fromName = ctx.short(from);
      const toName = ctx.short(to);
      return {
        ...base,
        hop: { from: fromName, to: toName },
        pieces: [
          m(titled(toName)),
          t(' took over from '),
          m(titled(fromName)),
          t(' after the conversation was too large.'),
        ],
      };
    }

    case 'failover_overflow_skipped': {
      const { to } = handoffPair(ev);
      const toName = ctx.short(to);
      const win = ctx.windowOf(to);
      return {
        ...base,
        pieces: [
          m(titled(toName)),
          t(
            win
              ? ` was skipped because the conversation does not fit its ${compactTokens(win)} window.`
              : ' was skipped because the conversation does not fit its window.',
          ),
        ],
      };
    }

    case 'failover_shrink_compacted': {
      const target = ev.to_model ?? ev.model;
      return {
        ...base,
        pieces: target
          ? [t('The conversation was compacted to fit '), m(titled(ctx.short(target))), t('.')]
          : [t('The conversation was compacted for a handoff.')],
      };
    }

    case 'failover_shrink_truncated': {
      const target = ev.to_model ?? ev.model;
      const note =
        ev.reason === 'tail_only'
          ? 'Only the most recent turns were kept.'
          : 'No compactor model was available, so the oldest turns were dropped.';
      return {
        ...base,
        note,
        pieces: target
          ? [t('The conversation was shortened to fit '), m(titled(ctx.short(target))), t('.')]
          : [t('The conversation was shortened for a handoff.')],
      };
    }

    case 'failover_shrink_failed': {
      const target = ev.to_model ?? ev.model;
      return {
        ...base,
        tone: 'attn',
        note: ev.reason === 'no_shrink_path' ? 'There was no way to reduce it any further.' : undefined,
        pieces: target
          ? [t('The conversation could not be shrunk to fit '), m(titled(ctx.short(target))), t('.')]
          : [t('The conversation could not be shrunk for a handoff.')],
      };
    }

    case 'overflow_chain_exhausted': {
      const routes =
        typeof ev.models_considered === 'number'
          ? ` after ${ev.models_considered} ${ev.models_considered === 1 ? 'route' : 'routes'}`
          : '';
      return {
        ...base,
        tone: 'attn',
        note: 'No enabled model has a window large enough for this conversation.',
        pieces: [m(titled(model)), t(` ran out of routes large enough for the conversation${routes}.`)],
      };
    }

    case 'upstream_context_overflow':
      return {
        ...base,
        pieces: [m(titled(model)), t(' rejected the request because the conversation is too large.')],
      };

    case 'openrouter_effort_clamped':
      return {
        ...base,
        pieces: [t('OpenRouter clamped the effort level for '), m(titled(model)), t('.')],
      };

    case 'openrouter_server_tools_stripped':
      return {
        ...base,
        pieces: [
          t('Server side tools were removed from a request to '),
          m(titled(model)),
          t(', which OpenRouter does not run.'),
        ],
      };

    case 'sanitized_error_substituted':
      return {
        ...base,
        pieces: [
          m(titled(model)),
          t(
            typeof ev.status === 'number'
              ? ` returned a ${ev.status} error; the router replaced the provider’s message with a safe one.`
              : ' returned an error; the router replaced the provider’s message with a safe one.',
          ),
        ],
      };

    case 'background_model_substituted':
      return {
        ...base,
        pieces: [
          t('A background step asked for a model this session does not have, so the router used '),
          m(titled(model)),
          t(' instead.'),
        ],
      };

    case 'anthropic_rate_limit_passthrough':
      return {
        ...base,
        pieces: [
          p(prov),
          t('’s rate limit was passed straight through to the client for '),
          m(titled(model)),
          t('.'),
        ],
      };

    case 'model_not_enabled':
      return {
        ...base,
        tone: 'attn',
        pieces: [
          t('A request asked for '),
          m(titled(model)),
          t(', which is not enabled for this session.'),
        ],
      };

    case 'session_root_selected':
      return {
        ...base,
        pieces: [t('The session started on '), m(titled(model)), t('.')],
      };

    case 'session_model_pinned':
      return {
        ...base,
        pieces: [t('The session is pinned to '), m(titled(model)), t(' on '), p(prov), t('.')],
      };

    case 'session_model_unpinned':
      return {
        ...base,
        pieces: [
          t('The session returned to its root model from '),
          m(titled(model)),
          t('.'),
        ],
      };

    case 'router_restarted':
      return { ...base, pieces: [t('The router restarted.')] };

    default: {
      // Never hidden. The raw name is shown so an unfamiliar event is still
      // searchable and reportable.
      const tail: Piece[] = ev.model ? [t(' for '), m(titled(model)), t('.')] : [t('.')];
      return { ...base, pieces: [t('The router recorded '), { t: 'raw', v: kind }, ...tail] };
    }
  }
}

// ---------------------------------------------------------------------------
// Timeline assembly
// ---------------------------------------------------------------------------

export interface QuietRow {
  type: 'quiet';
  id: string;
  model: string;
  count: number;
  /** "14 requests to Luna, all completed" */
  summary: string;
  events: AirEvent[];
  sentences: Sentence[];
  timestamp: string;
}

export interface SpokenRow {
  type: 'spoken';
  id: string;
  sentence: Sentence;
}

export type TimelineRow = QuietRow | SpokenRow;

export interface MinuteGroup {
  key: string;
  /** "08:58" */
  label: string;
  timestamp: string;
  rows: TimelineRow[];
}

export interface TimelineFilters {
  group?: KindGroup;
  model?: string | null;
}

function isQuietRequest(ev: AirEvent): boolean {
  return !ev.kind && ev.outcome === 'completed' && !ev.failover_from;
}

/**
 * Diagnostics that describe the same request event are absorbed into the
 * richer sentence. A 429 request is absorbed by its attempt. A success
 * diagnostic is absorbed by the completed failover request, which already
 * carries the source, target, duration and outcome. Unmatched diagnostics stay.
 */
/**
 * The router writes a request and its diagnostic in separate calls, so their
 * second-resolution timestamps can straddle a boundary. A few seconds of
 * tolerance keeps the pair together without merging unrelated moments.
 */
const PAIR_WINDOW_MS = 5000;

function near(a: string, b: string): boolean {
  const left = Date.parse(a);
  const right = Date.parse(b);
  if (!Number.isFinite(left) || !Number.isFinite(right)) return a === b;
  return Math.abs(left - right) <= PAIR_WINDOW_MS;
}

function absorbedEvents(events: AirEvent[]): Set<AirEvent> {
  const absorbed = new Set<AirEvent>();
  const attempts = events.filter(
    (e) => e.kind === 'rate_limit_failover_attempted' || e.kind === 'failover_overflow_attempted',
  );
  for (const ev of events) {
    if (ev.kind) continue;
    if (ev.outcome !== 'rate_limited' && ev.status !== 429) continue;
    const match = attempts.some((attempt) => {
      const from = attempt.from_model ?? attempt.failover_from;
      return near(attempt.timestamp, ev.timestamp) && from === ev.model;
    });
    if (match) absorbed.add(ev);
  }

  const successes = events.filter(
    (e) =>
      e.kind === 'rate_limit_failover_succeeded' || e.kind === 'failover_overflow_succeeded',
  );
  const completed = events.filter(
    (e) => !e.kind && e.outcome === 'completed' && !!e.failover_from,
  );
  for (const success of successes) {
    const from = success.from_model ?? success.failover_from;
    const to = success.to_model ?? success.model;
    const match = completed.some(
      (request) =>
        near(request.timestamp, success.timestamp) &&
        request.failover_from === from &&
        request.model === to,
    );
    if (match) absorbed.add(success);
  }
  return absorbed;
}

function successReasonFor(request: AirEvent, events: AirEvent[]): string | undefined {
  if (request.kind || request.outcome !== 'completed' || !request.failover_from) {
    return request.reason;
  }
  const success = events.find((event) => {
    if (
      event.kind !== 'rate_limit_failover_succeeded' &&
      event.kind !== 'failover_overflow_succeeded'
    ) {
      return false;
    }
    return (
      near(event.timestamp, request.timestamp) &&
      (event.from_model ?? event.failover_from) === request.failover_from &&
      (event.to_model ?? event.model) === request.model
    );
  });
  return success?.kind === 'rate_limit_failover_succeeded'
    ? 'rate_limit'
    : success?.kind === 'failover_overflow_succeeded'
      ? 'overflow'
      : request.reason;
}

function matchesFilters(ev: AirEvent, filters: TimelineFilters): boolean {
  const group = filters.group ?? 'all';
  if (group !== 'all' && !kindGroups(ev.kind).includes(group)) return false;
  if (filters.model && !eventModels(ev).includes(filters.model)) return false;
  return true;
}

/**
 * Group by minute, newest group first, newest row first inside a group.
 * Completed plain requests collapse per model per minute.
 */
export function buildTimeline(
  events: AirEvent[],
  ctx: SentenceContext,
  filters: TimelineFilters = {},
): MinuteGroup[] {
  const absorbed = absorbedEvents(events);
  const kept = events
    .filter((ev) => !absorbed.has(ev) && matchesFilters(ev, filters))
    .map((ev) => {
      const reason = successReasonFor(ev, events);
      return reason && reason !== ev.reason ? { ...ev, reason } : ev;
    });

  const buckets = new Map<string, AirEvent[]>();
  const order: string[] = [];
  for (const ev of kept) {
    const key = minuteKey(ev.timestamp);
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key)!.push(ev);
  }

  const groups: MinuteGroup[] = [];
  for (const key of order) {
    const inBucket = buckets.get(key)!;
    // Newest first inside the minute. Events sharing a timestamp keep their
    // source order reversed, so a cause reads below its consequence.
    const ordered = [...inBucket].reverse();

    const rows: TimelineRow[] = [];
    const quietByModel = new Map<string, QuietRow>();
    for (const ev of ordered) {
      if (isQuietRequest(ev)) {
        const model = ev.model ?? 'unknown';
        let row = quietByModel.get(model);
        if (!row) {
          row = {
            type: 'quiet',
            id: `${key}|quiet|${model}`,
            model,
            count: 0,
            summary: '',
            events: [],
            sentences: [],
            timestamp: ev.timestamp,
          };
          quietByModel.set(model, row);
          rows.push(row);
        }
        row.count += 1;
        row.events.push(ev);
        row.sentences.push(renderEvent(ev, ctx));
        continue;
      }
      rows.push({
        type: 'spoken',
        id: `${ev.timestamp}|${ev.kind ?? 'request'}|${rows.length}`,
        sentence: renderEvent(ev, ctx),
      });
    }

    for (const row of quietByModel.values()) {
      const name = titled(ctx.short(row.model));
      row.summary =
        row.count === 1
          ? `1 request to ${name}, completed`
          : `${row.count} requests to ${name}, all completed`;
    }

    const newest = ordered[0]?.timestamp ?? inBucket[0].timestamp;
    groups.push({ key, label: key.slice(11), timestamp: newest, rows });
  }

  // Newest minute group first.
  groups.sort((a, b) => (a.key < b.key ? 1 : a.key > b.key ? -1 : 0));
  return groups;
}
